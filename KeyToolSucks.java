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
        char[] password = args[1].toCharArray();
        keystore.load(new FileInputStream(args[0]), password);
        byte[] cert_bytes = null;
        for(Enumeration<String> aliases = keystore.aliases(); aliases.hasMoreElements();) {
            String nxt = aliases.nextElement();
            try {
                cert_bytes = ((KeyStore.TrustedCertificateEntry)keystore.getEntry(
                    nxt, null)).getTrustedCertificate().getEncoded();  //lol, Java
            } catch (Exception e) { //exception that gets raised varies between environments
                cert_bytes = ((KeyStore.PrivateKeyEntry)keystore.getEntry(
                    nxt, new KeyStore.PasswordProtection(password))
                    ).getCertificate().getEncoded();
            }

            for(int j=0; j<cert_bytes.length; j++) {
                System.out.print(String.format("%02X", cert_bytes[j]));
            }
            System.out.print("\n");
        }
        System.out.flush();
    }

}
