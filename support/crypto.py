
import jks
import openssl

DEFAULT_SSL_METHOD = openssl.TLSv1_method


class SSLContext(openssl.Context):
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

    @classmethod
    def from_jks(cls, jks_path, **kwargs):
        method = kwargs.pop('method', DEFAULT_SSL_METHOD)
        passphrase = kwargs.pop('passphrase', None)
        if kwargs:
            raise TypeError('unexpected keyword arguments: %r' % kwargs.keys())

        jks = jks.KeyStore.load(jks_path, passphrase)
        # set up basic elements -- private key, trusted certs, public cert + chain
        pkey = openssl.PrivateKey(ks.private_keys[0].pkey)
        trusted_certs = [openssl.Certificate(cert.cert) for cert in ks.certs]
        public_cert = openssl.Certificate(ks.private_keys[0].cert_chain[0][1])
        public_cert_chain = [openssl.Certificate(cert[1])
                             for cert in ks.private_keys[0].cert_chain[1:]]
        # set up OpenSSL SSLCTX object
        ctx = cls(method)
        ctx.use_privatekey(pkey)
        ctx.use_certificate(public_cert)
        public_cert.release_pointer()
        for cert in ks.private_keys[0].cert_chain[1:]:
            cert = openssl.Certificate(cert[1])
            cert.release_pointer()
            ctx.add_extra_chain_cert(cert)
        # want to know ASAP if there is a problem
        ctx.check_privatekey()
        cert_store = ctx.get_cert_store()
        # OpenSSL will automatically construct cert chain
        for cert in trusted_certs + public_cert_chain + [public_cert]:
            if cert not in cert_store:
                try:
                    cert_store.add_cert(cert)
                except ValueError:
                    pass  # why are certs already in cert store?
        return ctx




def _jks2context(jks_file, passphrase):
    ks = jks.KeyStore.load(jks_file, passphrase)
    # set up basic elements -- private key, trusted certs, public cert + chain
    pkey = openssl.PrivateKey(ks.private_keys[0].pkey)
    trusted_certs = [openssl.Certificate(cert.cert) for cert in ks.certs]
    public_cert = openssl.Certificate(ks.private_keys[0].cert_chain[0][1])
    public_cert_chain = [openssl.Certificate(cert[1])
                         for cert in ks.private_keys[0].cert_chain[1:]]
    # set up OpenSSL SSLCTX object
    ctx = openssl.Context(openssl.TLSv1_method)
    ctx.use_privatekey(pkey)
    ctx.use_certificate(public_cert)
    public_cert.release_pointer()
    for cert in ks.private_keys[0].cert_chain[1:]:
        cert = openssl.Certificate(cert[1])
        cert.release_pointer()
        ctx.add_extra_chain_cert(cert)
    # want to know ASAP if there is a problem
    ctx.check_privatekey()
    cert_store = ctx.get_cert_store()
    # OpenSSL will automatically construct cert chain
    for cert in trusted_certs + public_cert_chain + [public_cert]:
        if cert not in cert_store:
            try:
                cert_store.add_cert(cert)
            except ValueError:
                pass  # why are certs already in cert store?
    return ctx
