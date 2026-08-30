package com.devinmobile.app;

import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import androidx.core.content.FileProvider;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

@CapacitorPlugin(name = "ApkInstaller")
public class ApkInstallerPlugin extends Plugin {

    @PluginMethod
    public void downloadAndInstall(PluginCall call) {
        String apkUrl = call.getString("url");
        if (apkUrl == null) {
            call.reject("url is required");
            return;
        }

        String finalUrl = apkUrl;
        getActivity().runOnUiThread(() -> {
            try {
                // Descargar el APK
                URL url = new URL(finalUrl);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setInstanceFollowRedirects(true);
                conn.connect();

                // Seguir redirects de GitHub
                int responseCode = conn.getResponseCode();
                if (responseCode == HttpURLConnection.HTTP_MOVED_PERM ||
                    responseCode == HttpURLConnection.HTTP_MOVED_TEMP ||
                    responseCode == HttpURLConnection.HTTP_SEE_OTHER) {
                    String newUrl = conn.getHeaderField("Location");
                    conn.disconnect();
                    conn = (HttpURLConnection) new URL(newUrl).openConnection();
                    conn.setInstanceFollowRedirects(true);
                    conn.connect();
                }

                File outputFile = new File(getActivity().getExternalCacheDir(), "devinmobile_update.apk");
                FileOutputStream fos = new FileOutputStream(outputFile);
                InputStream is = conn.getInputStream();

                byte[] buffer = new byte[8192];
                int len;
                while ((len = is.read(buffer)) != -1) {
                    fos.write(buffer, 0, len);
                }
                fos.close();
                is.close();
                conn.disconnect();

                // Instalar el APK
                Intent intent = new Intent(Intent.ACTION_VIEW);
                Uri apkUri;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                    apkUri = FileProvider.getUriForFile(getActivity(),
                        getActivity().getPackageName() + ".fileprovider", outputFile);
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                } else {
                    apkUri = Uri.fromFile(outputFile);
                }
                intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                getContext().startActivity(intent);

                JSObject ret = new JSObject();
                ret.put("ok", true);
                call.resolve(ret);
            } catch (Exception e) {
                JSObject ret = new JSObject();
                ret.put("ok", false);
                ret.put("error", e.getMessage());
                call.resolve(ret);
            }
        });
    }
}
