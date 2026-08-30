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
        final String apkUrl = call.getString("url");
        if (apkUrl == null) {
            call.reject("url is required");
            return;
        }

        // Descargar en hilo de fondo
        new Thread(() -> {
            try {
                String downloadUrl = apkUrl;
                File outputFile = new File(getActivity().getExternalCacheDir(), "devinmobile_update.apk");

                // Seguir redirects manualmente (GitHub usa varios)
                HttpURLConnection conn = null;
                for (int i = 0; i < 5; i++) {
                    URL url = new URL(downloadUrl);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setInstanceFollowRedirects(false);
                    conn.setConnectTimeout(30000);
                    conn.setReadTimeout(60000);
                    conn.setRequestProperty("User-Agent", "DevinMobile-Android");
                    conn.connect();

                    int code = conn.getResponseCode();
                    if (code == HttpURLConnection.HTTP_MOVED_PERM ||
                        code == HttpURLConnection.HTTP_MOVED_TEMP ||
                        code == HttpURLConnection.HTTP_SEE_OTHER ||
                        code == 307 || code == 308) {
                        String location = conn.getHeaderField("Location");
                        conn.disconnect();
                        if (location == null || location.isEmpty()) {
                            call.resolve(new JSObject() {{
                                put("ok", false);
                                put("error", "Redirect sin Location");
                            }});
                            return;
                        }
                        downloadUrl = location;
                        continue;
                    }
                    break;
                }

                if (conn == null || conn.getResponseCode() != 200) {
                    int code = conn != null ? conn.getResponseCode() : -1;
                    if (conn != null) conn.disconnect();
                    final int finalCode = code;
                    call.resolve(new JSObject() {{
                        put("ok", false);
                        put("error", "HTTP " + finalCode);
                    }});
                    return;
                }

                // Descargar
                InputStream is = conn.getInputStream();
                FileOutputStream fos = new FileOutputStream(outputFile);
                byte[] buffer = new byte[8192];
                int len;
                while ((len = is.read(buffer)) != -1) {
                    fos.write(buffer, 0, len);
                }
                fos.close();
                is.close();
                conn.disconnect();

                // Instalar en UI thread
                getActivity().runOnUiThread(() -> {
                    try {
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

                        call.resolve(new JSObject() {{
                            put("ok", true);
                        }});
                    } catch (Exception e) {
                        call.resolve(new JSObject() {{
                            put("ok", false);
                            put("error", "Install: " + e.getMessage());
                        }});
                    }
                });
            } catch (final Exception e) {
                call.resolve(new JSObject() {{
                    put("ok", false);
                    put("error", e.getMessage());
                }});
            }
        }).start();
    }
}
