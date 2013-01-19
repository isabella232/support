/*
This class exists because KeyTool kind of sucks.
That's right, I said it.  KeyTool sucks.
*/

import java.security.KeyStore;
import java.io.FileInputStream;
import java.util.Enumeration;
import java.util.ArrayList;
import java.security.UnrecoverableKeyException;

public class KeyToolSucks {
    public static void main(String[] args) throws Exception {
        KeyStore keystore = KeyStore.getInstance("jks");
        char[] password = args[1].toCharArray();
        keystore.load(new FileInputStream(args[0]), password);
        ArrayList<byte[]> trusted_cert_bytes = new ArrayList<byte[]>();
        byte[] pkey_bytes = null;
        byte[] pub_cert_bytes = null;
        for(Enumeration<String> aliases = keystore.aliases(); aliases.hasMoreElements();) {
            String nxt = aliases.nextElement();
            try {
                trusted_cert_bytes.add(((KeyStore.TrustedCertificateEntry)keystore.getEntry(
                    nxt, null)).getTrustedCertificate().getEncoded());  //lol, Java
            } catch (Exception e) { //exception that gets raised varies between environments
                KeyStore.PrivateKeyEntry pkey = (KeyStore.PrivateKeyEntry)keystore.getEntry(
                    nxt, new KeyStore.PasswordProtection(password));
                pub_cert_bytes = pkey.getCertificate().getEncoded();
                pkey_bytes = pkey.getPrivateKey().getEncoded();
            }
        }
        //print out JSON formatted data
        System.out.println("{");
        System.out.print("\"pub_cert_bytes\":");
        print_bytes(pub_cert_bytes);
        System.out.println(",");
        System.out.print("\"pkey_bytes\":");
        print_bytes(pkey_bytes);
        System.out.println(",");
        System.out.print("\"trusted_cert_bytes\":[");
        for(int i=0; i<trusted_cert_bytes.size(); i++) {
            print_bytes(trusted_cert_bytes.get(i));
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
            System.out.print(String.format("%02X", b[j]));
        }
        System.out.print("\"");
    }

}
