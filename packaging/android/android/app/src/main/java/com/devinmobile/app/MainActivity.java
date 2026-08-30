package com.devinmobile.app;

import com.getcapacitor.BridgeActivity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ApkInstallerPlugin.class);
        super.onCreate(savedInstanceState);

        // Configurar WebView para permitir camara (QR scanner)
        WebView webView = this.bridge.getWebView();
        if (webView != null) {
            // Permitir autoplay de media
            webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
            // Conceder permisos de camara automaticamente desde el webview
            webView.setWebChromeClient(new WebChromeClient() {
                @Override
                public void onPermissionRequest(final PermissionRequest request) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            request.grant(request.getResources());
                        }
                    });
                }
            });
        }
    }
}
