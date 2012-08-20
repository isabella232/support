'''
CIP -- pronounced "sip"
C/C++ Interaction Protocol.
Don't wan't to take a whole SWIG?  How about just a little sip?

Provided the C funcionts in an .so follow some simple naming conventions,
this module will parse the C/C++ source code, extract the function definitions,
and generate very high level bindings.

(Currently, function pointer types are not supported.)

This is the naming convention:

typename_new -- instantiating a type, returning a void*
typename_delete -- deleting a type, taking a void*
typename_foo -- member function foo of type, whose first
parameter is the void* returned by typename_new

'''
import re
import os
import abc
import ctypes
import types

class CppException(Exception): pass

#TODO: should probably create a new Library class instead of smashing
#the ctypes object

def setup_library(library_file, source_files, errcheck = "check_exception",
                  base_exception=CppException):
    #1- load library
    if os.name in ["nt"]:
        print "running on windows platform; cannot load .so"
        return None
    else:
        library = ctypes.cdll.LoadLibrary(library_file)
    #2 - setup the exception checking function
    check_exception = getattr(library, errcheck)
    if base_exception is not CppException:
        base_exception = type("CppException", (CppException, base_exception), {})
    def errcheck(result, func, args):
        exc = check_exception()
        exc = ctypes.c_char_p(exc).value
        if exc is None or len(exc) == 0:
            return result
        raise base_exception(exc)
    #3- setup functions from source files
    finfos = []
    for f in source_files:
        with open(f) as fp:
            finfos += parse(fp.read())
    for finfo in finfos:
        c_func = getattr(library, finfo.name)
        finfo.func = c_func
        c_func.argtypes = finfo.argtypes
        c_func.restype  = finfo.restype
        if c_func != check_exception:
            c_func.errcheck = errcheck
        setattr(library, finfo.name, finfo)
        #TODO: new library class?
    #4- setup types that can be inferred
    for wrapped in wrap_all_c_types(finfos):
        setattr(library, wrapped.__name__, wrapped)
    return library

class CppClass(object):
    def __new__(cls, *a, **kw):
        self = object.__new__(cls)
        if "cobj_p" in kw:
            self.cobj_p = kw["cobj_p"]
        else:
            self.cobj_p = self.new(*a, **kw)
            #ensure that "child" objects passed in to the constructor are not
            #deleted during the lifetime of the current object
            self._child_objs = [o for o in list(a)+kw.values()
                                if isinstance(o, CppClass)]
        return self
    @property
    def _as_parameter_(self):
        return self.cobj_p
    def __del__(self):
        self.delete()
    @abc.abstractmethod
    def new(self, *a, **kw): pass
    @abc.abstractmethod
    def delete(self): pass

def wrap_all_c_types(func_infos):
    prefix_funcs = {}
    for f in func_infos:
        if f.name.endswith("_new"):
            prefix_funcs[f.name[:-3]] = []
    for f in func_infos:
        for p in prefix_funcs:
            if f.name.startswith(p):
                prefix_funcs[p].append(f)
    return [wrap_c_type(funcs) for funcs in prefix_funcs.values()]

def wrap_c_type(functions):
    members = []
    new     = None
    delete  = None
    prefix  = ""
    for f in functions:
        if   f.name.endswith("_new"):
            prefix = f.name[:-3] #get rid of 'new'
            new = f
        elif f.name.endswith("_delete"):
            delete = f
        else:
            members.append(f)
    attrs = dict([(m.name.replace(prefix, ""), m) for m in members])
    attrs["new"] = staticmethod(new) #new doesn't take self, special case
    return type(prefix.title().replace("_", ""), (CppClass,), attrs)

def resolve_call_args(fname, arg_list, args, kwargs):
    pos_args = arg_list[:len(args)]
    #check that no arguments are double specified in positional and keyword
    for arg in pos_args:
        if arg in kwargs:
            raise TypeError(fname+" got multiple values for keyword argument: '"+arg+"'") 
    #check not too many arguments
    if len(args) > len(pos_args):
        raise TypeError(fname+" takes exactly " + str(len(pos_args)) + \
                        " arguments (" + str(len(args)) + " given) ")
    #check no unexpected keyword arguments
    non_pos_argset = set(arg_list[len(args):])
    for k in kwargs:
        if k not in non_pos_argset:
            raise TypeError(fname+" got an unexpected keyword argument '"+k+"'")
    #assign positional arguments to dictionary
    arg_dict = dict(zip(pos_args, args))
    #assign keyword arguments to dictionary
    arg_dict.update(kwargs)
    if len(arg_dict) != len(arg_list):
        raise TypeError(fname+"() takes exactly "+str(len(arg_list))+\
            " arguments ("+str(len(arg_dict))+" given)")
    return arg_dict

class CFuncInfo(object):    
    def __init__(self, name, restype=None, argtypes=None, argnames=None):
        self.name = name
        self.restype  = restype
        self.argtypes = argtypes
        self.argnames = argnames
        self.func = None
        self.arg_list = [(a or "arg"+str(i)) for i,a in enumerate(self.argnames)]
    
    def __call__(self, *a, **kw):
        arg_dict = resolve_call_args(self.name, self.arg_list, a, kw)
        return self.func(*[arg_dict[a] for a in self.arg_list])
    
    def __get__(self, obj, objtype=None):
        if obj is not None:
            def as_method(*a, **kw):
                return self.__call__(*a, **kw)
            as_method.__name__ = self.name
            return types.MethodType(as_method, obj, objtype)
        return self
    
    def __repr__(self): return "CFuncInfo(name="+self.name+", restype="+\
        repr(self.restype)+", argtypes="+repr(self.argtypes)+\
        ", arg_list="+repr(self.arg_list)+")"

def parse(data):
    #step 1 -- eliminate comments
    data = comment_remover(data)
    #step 2 -- eliminate string literals
    data = string_literal_remover(data)
    #step 3 -- find the extern directive
    extern_blocks = data.split("extern")
    #step 4 -- remove sub-blocks
    data = "\n".join([remove_sub_blocks(b) for b in extern_blocks])
    func_defs = data.split(")")
    func_defs = [fd.strip() for fd in func_defs]
    func_defs = [fd for fd in func_defs if fd != ""]
    return [c_func2ctypes(fd) for fd in func_defs]

def c_func2ctypes(definition):
    '''
    Processes a C-Style function declaration.
    rettype funcname(paramtype paramname, ...)
    For example:
    int add(int a, int b)
    '''
    type_funcname, _, param_list = definition.partition("(")
    restype = parse_type(type_funcname)
    func_name = type_funcname.split()[-1].replace('*', '')
    param_list, _, _ = param_list.partition(")")
    param_list = param_list.split(",")
    if param_list == [""]:
        argtypes = []
        argnames = []
    else:
        argtypes = [parse_type(p) for p in param_list]
        argnames = [parse_name(p) for p in param_list]
    return CFuncInfo(func_name, restype, argtypes, argnames)
    
TYPE_KEYWORDS = [
     "char",  "wchar",  "short",  "int",  "long",
    "uchar", "uwchar", "ushort", "uint", "ulong",
    "float", "double", "size_t", '*', "void",
    "_Bool", "unsigned"
    ]

POINTER_TYPES = {}

#NOTE: can't parse function pointer types for now
def parse_name(s):
    s = s.replace('*', '').replace(',', '').replace(')', '')
    return ([a for a in s.split() if a not in TYPE_KEYWORDS] + [None])[0]

def parse_type(s):
    '''
    Parse a C type out of a string which contains exactly one C type definition.
    '''
    kw_counts = dict([(kw, s.count(kw)) for kw in TYPE_KEYWORDS])
    pointer_count = kw_counts['*']
    #owch... gnarly decision tree among C types
    if kw_counts["char"]:
        if kw_counts["unsigned"] or kw_counts["uchar"]:
            param = ctypes.c_ubyte
        else:
            if kw_counts["*"] == 1:
                param = ctypes.c_char_p
                kw_counts["*"] = 0
            else:
                param = ctypes.c_char
    elif kw_counts["wchar"]:
        param = ctypes.c_wchar
    elif kw_counts["_Bool"]:
        param = ctypes.c_bool
    elif kw_counts["short"]:
        if kw_counts["unsigned"] or kw_counts["ushort"]:
            param = ctypes.c_ushort
        else:
            param = ctypes.c_short
    elif kw_counts["long"]:
        if kw_counts["unsigned"] or kw_counts["ulong"]:
            if kw_counts["long"] > 1:
                param = ctypes.c_ulonglong
            else:
                param = ctypes.c_ulong
        else:
            if kw_counts["long"] > 1:
                param = ctypes.c_longlong
            else:
                param = ctypes.c_long
    elif kw_counts["int"] or kw_counts["size_t"]: #short and long override int
        if kw_counts["unsigned"] or kw_counts["uint"] or kw_counts["size_t"]:
            param = ctypes.c_uint
        else:
            param = ctypes.c_int
    elif kw_counts["float"]:
        param = ctypes.c_float
    elif kw_counts["double"]:
        if kw_counts["long"]:
            param = ctypes.c_longdouble
        else:
            param = ctypes.c_double
    elif kw_counts["void"]:
        if kw_counts["*"] == 0:
            param = None #ctypes considers None equivalent to void
        else:
            param = ctypes.c_void_p
            kw_counts["*"] -= 1
    else:
        raise ValueError("unrecognized type: "+s)
    
    type_key = (param, kw_counts['*'])
    if type_key  not in POINTER_TYPES:
        for i in range(kw_counts['*']):
            param = ctypes.POINTER(param)
            #apologies to PEP-8; POINTER is factory function for creating
            #new c types of type pointer-to-(input type)
        POINTER_TYPES[type_key] = param
    return POINTER_TYPES[type_key]
    
#special thanks to MizardX on StackOverflow for this function
#http://stackoverflow.com/a/241506
def comment_remover(text):
    def replacer(match):
        s = match.group(0)
        if s.startswith('/'):
            return ""
        else:
            return s
    pattern = re.compile(
        r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
        re.DOTALL | re.MULTILINE
    )
    return re.sub(pattern, replacer, text)

def string_literal_remover(text):
    return "".join(text.split('"')[::2])
    #TODO: handle single quote strings -- since multi-character single quote
    #string is a compiler warning, don't worry about it yet
    #(the tricky part with two types of quotes is nesting -- '"' versus "'")
    
def find_all(s, c):
    positions = []
    pos = s.find(c)
    while pos != -1:
        positions.append(pos)
        pos = s.find(c, pos+1)
    return positions
    
def remove_sub_blocks(text):
    _, _, text = text.partition("{")
    depth = 1
    output = []
    ob_pos = find_all(text, "{") + [2**31]
    cb_pos = find_all(text, "}")
    ob_pos.reverse()
    cb_pos.reverse()
    cur_pos = 0
    while depth > 0 and len(cb_pos) > 0:
        if depth == 1:
            output.append(text[cur_pos:min([ob_pos[-1], cb_pos[-1]])])
        if ob_pos[-1] < cb_pos[-1]:
            cur_pos = ob_pos.pop()+1
            depth += 1
        else:
            cur_pos = cb_pos.pop()+1
            depth -= 1
    if depth != 0:
        raise ValueError("First block unterminated.")
    return "".join(output)

