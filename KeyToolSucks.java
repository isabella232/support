/*
NOTE: for maximum cross-compatibility, this is 1.4 compatible.
compile with -source 1.4 -target 1.4

This class exists because KeyTool kind of sucks.
That's right, I said it.  KeyTool sucks.
*/

import java.security.KeyStore;
import java.io.FileInputStream;
import java.io.ByteArrayOutputStream;
import java.util.Enumeration;
import java.util.ArrayList;
import java.security.UnrecoverableKeyException;

public class KeyToolSucks {
    public static void main(String[] args) throws Exception {
        KeyStore keystore = KeyStore.getInstance("jks");
        char[] password = args[1].toCharArray();
        keystore.load(new FileInputStream(args[0]), password);
        ArrayList/*<byte[]>*/ trusted_cert_bytes = new ArrayList/*<byte[]>*/();
        byte[] pkcs12_data = null;
        byte[] pub_cert_bytes = null;
        KeyStore.PasswordProtection pwd = new KeyStore.PasswordProtection(password);
        for(Enumeration/*<String>*/ aliases = keystore.aliases(); aliases.hasMoreElements();) {
            String nxt = (String)aliases.nextElement();
            try {
                trusted_cert_bytes.add(((KeyStore.TrustedCertificateEntry)keystore.getEntry(
                    nxt, null)).getTrustedCertificate().getEncoded());  //lol, Java
            } catch (Exception e) { //exception that gets raised varies between environments
                KeyStore.PrivateKeyEntry pkey = (KeyStore.PrivateKeyEntry)keystore.getEntry(nxt, pwd);
                pub_cert_bytes = pkey.getCertificate().getEncoded();
                KeyStore p12_keystore = KeyStore.getInstance("PKCS12");
                p12_keystore.load(null, null);
                p12_keystore.setEntry(nxt, keystore.getEntry(nxt, pwd), pwd);
                ByteArrayOutputStream p12_stream = new ByteArrayOutputStream();
                p12_keystore.store(p12_stream, password);
                pkcs12_data = p12_stream.toByteArray();
            }
        }
        //print out JSON formatted data
        System.out.println("{");
        System.out.print("\"pub_cert_bytes\":");
        print_bytes(pub_cert_bytes);
        System.out.println(",");
        System.out.print("\"pkcs12_data\":");
        print_bytes(pkcs12_data);
        System.out.println(",");
        System.out.print("\"trusted_cert_bytes\":[");
        for(int i=0; i<trusted_cert_bytes.size(); i++) {
            print_bytes((byte[])trusted_cert_bytes.get(i));
            if(i !=  trusted_cert_bytes.size() -1) {
                System.out.println(",");
            }
        }
        System.out.print("]\n}\n");
        System.out.flush();
    }

    private static void print_bytes(byte[] b) {
        System.out.print("\"");
        for(int j=0; j<b.length; j++) {
            String hex = Integer.toHexString(b[j] & 0xFF);
            if(hex.length() == 1) {
                System.out.print("0");
            }
            System.out.print(hex);
            //System.out.print(String.format("%02X", b[j]));
        }
        System.out.print("\"");
    }

}
