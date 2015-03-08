
from OpenSSL.SSL import Context, TLSv1_METHOD


DEFAULT_SSL_METHOD = TLSv1_METHOD


class SSLContext(Context):
    @classmethod
    def from_cert_key(cls, certfile, keyfile, **kwargs):
        method = kwargs.pop('method', DEFAULT_SSL_METHOD)
        ca_certs = kwargs.pop('ca_certs', None)
        passphrase = kwargs.pop('passphrase', None)
        if kwargs:
            raise TypeError('unexpected keyword arguments: %r' % kwargs.keys())
        ret = cls(method)
        ret.use_certificate_chain_file(certfile)
        if passphrase:
            ret.set_password(passphrase)
        ret.use_privatekey_file(keyfile)
        ret.check_privatekey()
        if ca_certs:
            ret.load_verify_locations(ca_certs)
        return ret
