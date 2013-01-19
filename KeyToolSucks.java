/*
This class exists because KeyTool kind of sucks.
That's right, I said it.  KeyTool sucks.
*/

import java.security.KeyStore;
import java.io.FileInputStream;
import java.util.Enumeration;
import java.security.UnrecoverableKeyException;

public class KeyToolSucks {
    public static void main(String[] args) throws Exception {
        KeyStore keystore = KeyStore.getInstance("jks");
        keystore.load(new FileInputStream(args[0]), args[1].toCharArray());
        for(Enumeration<String> aliases = keystore.aliases(); aliases.hasMoreElements();) {
            try {
                byte[] cert_bytes = ((KeyStore.TrustedCertificateEntry)keystore.getEntry(aliases.nextElement(), null)
                    ).getTrustedCertificate().getEncoded();  //lol, Java
                for(int j=0; j<cert_bytes.length; j++) {
                    System.out.print(String.format("%02X", cert_bytes[j]));
                }
                System.out.print("\n");
            } catch (UnrecoverableKeyException e) { }
        }
        System.out.flush();
    }
}
