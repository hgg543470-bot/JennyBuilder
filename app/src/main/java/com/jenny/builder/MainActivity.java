package com.jenny.builder;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.system.Os;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import android.database.Cursor;
import android.provider.OpenableColumns;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import org.json.JSONObject;

public class MainActivity extends AppCompatActivity {
    private static final int REQ_PERM_STORAGE = 1;
    private static final int REQ_PERM_NOTIF = 2;
    private static final int REQ_PICK_FOLDER = 42;
    private static final int REQ_PICK_TORRENT = 43;
    private static final int REQ_OVERLAY_PERM = 44;
    private static final int REQ_FILE_CHOOSER = 45;
    private static ValueCallback<Uri[]> filePathCallbackV166 = null;
    private static boolean autoAskedStorageOnceV166 = false;
    private static final int REQ_INSTALL_PERM = 46;
    private static final int REQ_UNINSTALL_PKG = 47;
    private static String pendingInstallApkPathV170 = null;
    private static String pendingUninstallPkgV170 = null;
    private static Runnable installPermWatchRunnableV170 = null;
    private static int installPermWatchTicksV170 = 0;
    private static Runnable uninstallWatchRunnableV170 = null;
    private static int uninstallWatchTicksV170 = 0;
    private static MainActivity instance;
    private static WebView webView;
    private static boolean isPageLoaded = false;
    private static String pendingTorrentPayload = null;
    private static String pendingTorrentName = null;
    private static boolean isVideoFullscreenV132 = false;
    private static boolean isVideoLockedV132 = false;
    private static boolean isVideoActiveV132 = false;
    private static boolean isDesktopOverlayActiveV132 = false;
    private static boolean pendingOverlayAfterGrantV147 = false;
    private static boolean waitingForPermScreenV148 = false;
    private static long permFlowLockUntilMsV152 = 0L;
    private static long overlayGrantProtectionUntilMsV153 = 0L;
    private static boolean subSettingsLaunchedV154 = false;
    private static int bringBackTicksV155 = 0;
    private static Runnable bringBackRunnableV155 = null;
    private static int permWatchTicksV147 = 0;
    private static final android.os.Handler permWatchHandlerV147 = new android.os.Handler(android.os.Looper.getMainLooper());
    private static Runnable permWatchRunnableV147 = null;
    private static Runnable storageWatchRunnableV165 = null;
    private static int storageWatchTicksV165 = 0;
    private static Runnable notifWatchRunnableV165 = null;
    private static int notifWatchTicksV165 = 0;
    private static boolean isOpeningSettingsV146 = false;
    private static int savedOverlayW = 0;
    private static int savedOverlayH = 0;
    private static int savedOverlayX = 24;
    private static int savedOverlayY = 140;
    private static android.widget.FrameLayout overlayContainerV132 = null;
    private static android.view.WindowManager wmV132 = null;
    private static android.view.WindowManager.LayoutParams overlayParamsV132 = null;

    private void handleIncomingTorrentIntent(Intent intent) {
        if (intent == null) return;
        try {
            String action = intent.getAction();
            if (Intent.ACTION_VIEW.equals(action)) {
                Uri data = intent.getData();
                if (data != null) {
                    String scheme = data.getScheme();
                    if ("magnet".equalsIgnoreCase(scheme)) {
                        pendingTorrentPayload = data.toString();
                        pendingTorrentName = "Magnet Link";
                    } else {
                        readTorrentUriToPending(data);
                    }
                }
            } else if (Intent.ACTION_SEND.equals(action)) {
                String sharedText = intent.getStringExtra(Intent.EXTRA_TEXT);
                if (sharedText != null && sharedText.contains("magnet:?")) {
                    int idx = sharedText.indexOf("magnet:?");
                    String mag = sharedText.substring(idx).trim().split("\\s+")[0];
                    pendingTorrentPayload = mag;
                    pendingTorrentName = "Shared Magnet";
                } else {
                    Uri streamUri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
                    if (streamUri != null) {
                        readTorrentUriToPending(streamUri);
                    }
                }
            }
            if (pendingTorrentPayload != null && webView != null) {
                webView.postDelayed(() -> evalJs("if(window.checkAndroidPendingTorrentV114) window.checkAndroidPendingTorrentV114();"), 250);
                webView.postDelayed(() -> evalJs("if(window.checkAndroidPendingTorrentV114) window.checkAndroidPendingTorrentV114();"), 1800);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private void readTorrentUriToPending(Uri uri) {
        try {
            String displayName = "external.torrent";
            if ("content".equalsIgnoreCase(uri.getScheme())) {
                try (Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
                    if (cursor != null && cursor.moveToFirst()) {
                        int nameIdx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                        if (nameIdx >= 0) {
                            String n = cursor.getString(nameIdx);
                            if (n != null && !n.isEmpty()) displayName = n;
                        }
                    }
                } catch (Exception ignored) {}
            } else if (uri.getLastPathSegment() != null) {
                displayName = uri.getLastPathSegment();
            }

            try (InputStream is = getContentResolver().openInputStream(uri)) {
                if (is == null) return;
                ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                byte[] chunk = new byte[16384];
                int nRead;
                while ((nRead = is.read(chunk, 0, chunk.length)) != -1) {
                    buffer.write(chunk, 0, nRead);
                }
                byte[] rawBytes = buffer.toByteArray();
                if (rawBytes.length > 0) {
                    pendingTorrentPayload = "B64TOR:" + Base64.encodeToString(rawBytes, Base64.NO_WRAP);
                    pendingTorrentName = displayName;
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    
    private void applyVideoSystemUiLockV132() {
        runOnUiThread(() -> {
            try {
                android.view.Window win = getWindow();
                if (win == null) return;
                View decor = win.getDecorView();
                boolean hideBars = isVideoFullscreenV132 || isVideoLockedV132;

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    android.view.WindowManager.LayoutParams lp = win.getAttributes();
                    lp.layoutInDisplayCutoutMode = hideBars
                        ? android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                        : android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT;
                    win.setAttributes(lp);
                }

                if (hideBars) {
                    win.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
                    decor.setSystemUiVisibility(flags);

                    if (Build.VERSION.SDK_INT >= 29 && webView != null) {
                        int w = Math.max(1080, webView.getWidth());
                        int h = Math.max(2400, webView.getHeight());
                        java.util.List<android.graphics.Rect> rects = new java.util.ArrayList<>();
                        rects.add(new android.graphics.Rect(0, h - 220, w, h));
                        rects.add(new android.graphics.Rect(0, 0, w, 160));
                        rects.add(new android.graphics.Rect(0, h / 2 - 200, 120, h / 2 + 200));
                        rects.add(new android.graphics.Rect(w - 120, h / 2 - 200, w, h / 2 + 200));
                        webView.setSystemGestureExclusionRects(rects);
                    }
                } else {
                    win.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    decor.setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
                    if (Build.VERSION.SDK_INT >= 29 && webView != null) {
                        webView.setSystemGestureExclusionRects(java.util.Collections.emptyList());
                    }
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
    }

    // V1.9: гарантируем, что WebView принимает фокус и ввод с клавиатуры
    private void ensureWebViewFocusV19() {
        try {
            if (webView == null) return;
            webView.setFocusable(true);
            webView.setFocusableInTouchMode(true);
            if (!webView.hasFocus()) {
                webView.requestFocus(View.FOCUS_DOWN);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && !isDesktopOverlayActiveV132) {
            ensureWebViewFocusV19();
        }
        if (hasFocus && isDesktopOverlayActiveV132) {
            if (android.os.SystemClock.uptimeMillis() < overlayGrantProtectionUntilMsV153) {
                moveTaskToBack(true);
                return;
            }
            if (isDesktopOverlayActiveV132 && android.os.SystemClock.uptimeMillis() < overlayGrantProtectionUntilMsV153) {
            moveTaskToBack(true);
            return;
        }
        restoreWebViewToActivityV135(false);
        }
        if (hasFocus && (isVideoFullscreenV132 || isVideoLockedV132)) {
            applyVideoSystemUiLockV132();
        }
    }

    @Override
    public void onBackPressed() {
        if (isVideoLockedV132) {
            evalJs("if(window.showVideoFeedbackV130) window.showVideoFeedbackV130('🔒 Управление заблокировано');");
            applyVideoSystemUiLockV132();
            return;
        }
        if (isVideoFullscreenV132) {
            evalJs("if(window.toggleVideoNativeFullscreenV132) window.toggleVideoNativeFullscreenV132();");
            return;
        }
        super.onBackPressed();
    }

    
    private void enterDesktopOverlayInternalV133() {
        runOnUiThread(() -> {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(MainActivity.this)) {
                    return;
                }
                if (isDesktopOverlayActiveV132 || webView == null) return;
                wmV132 = (android.view.WindowManager) getApplicationContext().getSystemService(WINDOW_SERVICE);
                android.util.DisplayMetrics dm = getResources().getDisplayMetrics();
                float density = dm.density;
                final int maxHwW = Math.max(1080, dm.widthPixels);
                final int maxHwH = Math.max(1920, dm.heightPixels);

                int wPx = (savedOverlayW > 48) ? Math.min(maxHwW, savedOverlayW) : Math.round(300 * density);
                int hPx = (savedOverlayH > 32) ? Math.min(maxHwH, savedOverlayH) : Math.round(176 * density);

                int type = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                        ? android.view.WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : android.view.WindowManager.LayoutParams.TYPE_PHONE;

                overlayParamsV132 = new android.view.WindowManager.LayoutParams(
                        wPx, hPx, type,
                        android.view.WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                | android.view.WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                                | android.view.WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                        android.graphics.PixelFormat.TRANSLUCENT
                );
                overlayParamsV132.gravity = android.view.Gravity.TOP | android.view.Gravity.START;
                overlayParamsV132.x = savedOverlayX;
                overlayParamsV132.y = savedOverlayY;

                if (webView.getParent() instanceof android.view.ViewGroup) {
                    ((android.view.ViewGroup) webView.getParent()).removeView(webView);
                }
                webView.setScaleX(1.0f);
                webView.setScaleY(1.0f);

                overlayContainerV132 = new android.widget.FrameLayout(getApplicationContext()) {
                    private float downRawX = 0f, downRawY = 0f;
                    private int startWinX = 0, startWinY = 0;
                    private boolean isDragging = false;
                    private boolean isPinching = false;
                    private float initSpan = 1f, initFocusX = 0f, initFocusY = 0f;
                    private int pinchStartW = 0, pinchStartH = 0, pinchStartX = 0, pinchStartY = 0;
                    private int virtualW = wPx, virtualH = hPx;
                    private long lastLayoutUpdateMs = 0L;

                    private float getRawXSafe(android.view.MotionEvent ev, int idx) {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            return ev.getRawX(idx);
                        }
                        return (ev.getRawX() - ev.getX()) + ev.getX(idx);
                    }

                    private float getRawYSafe(android.view.MotionEvent ev, int idx) {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            return ev.getRawY(idx);
                        }
                        return (ev.getRawY() - ev.getY()) + ev.getY(idx);
                    }

                    private void cancelWebViewTouch(android.view.MotionEvent ev) {
                        android.view.MotionEvent cancelEv = android.view.MotionEvent.obtain(ev);
                        cancelEv.setAction(android.view.MotionEvent.ACTION_CANCEL);
                        super.dispatchTouchEvent(cancelEv);
                        cancelEv.recycle();
                    }

                    private void applyVirtualSizeAndPos(int targetW, int targetH, int targetX, int targetY, boolean forceSync) {
                        virtualW = Math.max(48, Math.min(maxHwW * 3, targetW));
                        virtualH = Math.max(32, Math.min(maxHwH * 3, targetH));

                        // Ограничиваем физический буфер SurfaceFlinger размером экрана, а все что больше — дожимаем GPU-масштабом WebView!
                        int hwW = Math.min(maxHwW, virtualW);
                        int hwH = Math.min(maxHwH, virtualH);
                        float extraScaleX = (float) virtualW / Math.max(1, hwW);
                        float extraScaleY = (float) virtualH / Math.max(1, hwH);

                        if (webView != null) {
                            webView.setPivotX(0f);
                            webView.setPivotY(0f);
                            webView.setScaleX(extraScaleX);
                            webView.setScaleY(extraScaleY);
                        }

                        overlayParamsV132.x = targetX;
                        overlayParamsV132.y = targetY;
                        savedOverlayX = targetX;
                        savedOverlayY = targetY;

                        long now = android.os.SystemClock.uptimeMillis();
                        boolean sizeChanged = (Math.abs(hwW - overlayParamsV132.width) >= 6) || (Math.abs(hwH - overlayParamsV132.height) >= 6);
                        if (forceSync || !sizeChanged || (now - lastLayoutUpdateMs >= 28L)) {
                            overlayParamsV132.width = hwW;
                            overlayParamsV132.height = hwH;
                            savedOverlayW = hwW;
                            savedOverlayH = hwH;
                            if (sizeChanged) lastLayoutUpdateMs = now;
                            try {
                                wmV132.updateViewLayout(overlayContainerV132, overlayParamsV132);
                            } catch (Exception ignored) {}
                        }
                    }

                    @Override
                    public boolean dispatchTouchEvent(android.view.MotionEvent ev) {
                        if (!isDesktopOverlayActiveV132 || wmV132 == null || overlayParamsV132 == null) {
                            return super.dispatchTouchEvent(ev);
                        }
                        int action = ev.getActionMasked();

                        if (action == android.view.MotionEvent.ACTION_DOWN) {
                            downRawX = getRawXSafe(ev, 0);
                            downRawY = getRawYSafe(ev, 0);
                            startWinX = overlayParamsV132.x;
                            startWinY = overlayParamsV132.y;
                            if (virtualW < 48) virtualW = overlayParamsV132.width;
                            if (virtualH < 32) virtualH = overlayParamsV132.height;
                            isDragging = false;
                            isPinching = false;
                            return super.dispatchTouchEvent(ev);
                        }

                        if (action == android.view.MotionEvent.ACTION_POINTER_DOWN && ev.getPointerCount() >= 2) {
                            if (!isPinching) {
                                cancelWebViewTouch(ev);
                            }
                            isPinching = true;
                            isDragging = false;
                            float rx0 = getRawXSafe(ev, 0);
                            float ry0 = getRawYSafe(ev, 0);
                            float rx1 = getRawXSafe(ev, 1);
                            float ry1 = getRawYSafe(ev, 1);
                            initSpan = (float) Math.max(12.0, Math.hypot(rx1 - rx0, ry1 - ry0));
                            initFocusX = (rx0 + rx1) * 0.5f;
                            initFocusY = (ry0 + ry1) * 0.5f;
                            pinchStartW = Math.max(48, virtualW);
                            pinchStartH = Math.max(32, virtualH);
                            pinchStartX = overlayParamsV132.x;
                            pinchStartY = overlayParamsV132.y;
                            return true;
                        }

                        if (action == android.view.MotionEvent.ACTION_MOVE) {
                            if (isPinching && ev.getPointerCount() >= 2) {
                                float rx0 = getRawXSafe(ev, 0);
                                float ry0 = getRawYSafe(ev, 0);
                                float rx1 = getRawXSafe(ev, 1);
                                float ry1 = getRawYSafe(ev, 1);
                                float curSpan = (float) Math.max(12.0, Math.hypot(rx1 - rx0, ry1 - ry0));
                                float curFocusX = (rx0 + rx1) * 0.5f;
                                float curFocusY = (ry0 + ry1) * 0.5f;
                                float scale = curSpan / initSpan;

                                int newW = Math.max(48, Math.round(pinchStartW * scale));
                                int newH = Math.max(32, Math.round(pinchStartH * scale));
                                float actualScaleX = (float) newW / Math.max(1, pinchStartW);
                                float actualScaleY = (float) newH / Math.max(1, pinchStartH);

                                int newX = Math.round(curFocusX - (initFocusX - pinchStartX) * actualScaleX);
                                int newY = Math.round(curFocusY - (initFocusY - pinchStartY) * actualScaleY);
                                applyVirtualSizeAndPos(newW, newH, newX, newY, false);
                                return true;
                            } else if (!isPinching && ev.getPointerCount() == 1) {
                                float dx = getRawXSafe(ev, 0) - downRawX;
                                float dy = getRawYSafe(ev, 0) - downRawY;
                                if (!isDragging && Math.hypot(dx, dy) > 8.0) {
                                    isDragging = true;
                                    cancelWebViewTouch(ev);
                                }
                                if (isDragging) {
                                    overlayParamsV132.x = Math.round(startWinX + dx);
                                    overlayParamsV132.y = Math.round(startWinY + dy);
                                    savedOverlayX = overlayParamsV132.x;
                                    savedOverlayY = overlayParamsV132.y;
                                    try {
                                        wmV132.updateViewLayout(overlayContainerV132, overlayParamsV132);
                                    } catch (Exception ignored) {}
                                    return true;
                                }
                            }
                            return super.dispatchTouchEvent(ev);
                        }

                        if (action == android.view.MotionEvent.ACTION_POINTER_UP) {
                            if (ev.getPointerCount() <= 2) {
                                int remIdx = (ev.getActionIndex() == 0) ? 1 : 0;
                                downRawX = getRawXSafe(ev, remIdx);
                                downRawY = getRawYSafe(ev, remIdx);
                                startWinX = overlayParamsV132.x;
                                startWinY = overlayParamsV132.y;
                                applyVirtualSizeAndPos(virtualW, virtualH, startWinX, startWinY, true);
                                isPinching = false;
                                isDragging = true;
                            }
                            return true;
                        }

                        if (action == android.view.MotionEvent.ACTION_UP || action == android.view.MotionEvent.ACTION_CANCEL) {
                            if (isDragging || isPinching) {
                                applyVirtualSizeAndPos(virtualW, virtualH, overlayParamsV132.x, overlayParamsV132.y, true);
                                isDragging = false;
                                isPinching = false;
                                return true;
                            }
                        }
                        return super.dispatchTouchEvent(ev);
                    }
                };

                overlayContainerV132.setBackgroundColor(0xFF000000);
                overlayContainerV132.addView(webView, new android.widget.FrameLayout.LayoutParams(
                        android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                        android.view.ViewGroup.LayoutParams.MATCH_PARENT
                ));
                wmV132.addView(overlayContainerV132, overlayParamsV132);
                isDesktopOverlayActiveV132 = true;
                evalJs("if(window.onDesktopOverlayStateChangedV133) window.onDesktopOverlayStateChangedV133(true);");
            } catch (Exception e) {
                e.printStackTrace();
                restoreWebViewToActivityV135(false);
            }
        });
    }

    private void stopBringBackLoopV155() {
        if (bringBackRunnableV155 != null) {
            permWatchHandlerV147.removeCallbacks(bringBackRunnableV155);
            bringBackRunnableV155 = null;
        }
    }

    private void stopOverlayPermWatcherV147() {
        stopBringBackLoopV155();
        if (permWatchRunnableV147 != null) {
            permWatchHandlerV147.removeCallbacks(permWatchRunnableV147);
            permWatchRunnableV147 = null;
        }
    }

    private void bringMainActivityToFrontV149() {
        if (subSettingsLaunchedV154 || !waitingForPermScreenV148) {
            stopBringBackLoopV155();
            return;
        }
        try {
            android.app.ActivityManager am = (android.app.ActivityManager) getSystemService(android.content.Context.ACTIVITY_SERVICE);
            if (am != null) {
                am.moveTaskToFront(getTaskId(), android.app.ActivityManager.MOVE_TASK_WITH_HOME);
            }
        } catch (Exception ignored) {}
        try {
            Intent i = new Intent(getApplicationContext(), MainActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            android.app.PendingIntent pi = android.app.PendingIntent.getActivity(
                getApplicationContext(), 149, i,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE
            );
            if (Build.VERSION.SDK_INT >= 34) {
                android.app.ActivityOptions opts = android.app.ActivityOptions.makeBasic();
                try {
                    android.app.ActivityOptions.class
                        .getMethod("setPendingIntentBackgroundActivityStartMode", int.class)
                        .invoke(opts, 1);
                } catch (Throwable ignored) {}
                pi.send(getApplicationContext(), 0, null, null, null, null, opts.toBundle());
            } else {
                pi.send();
            }
        } catch (Exception ignored) {}
        try {
            Intent i2 = new Intent(getApplicationContext(), MainActivity.class);
            i2.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            getApplicationContext().startActivity(i2);
        } catch (Exception ignored) {}
    }

    private String getSignatureSha256V170(android.content.pm.PackageInfo pi) {
        try {
            if (pi == null) return "";
            android.content.pm.Signature[] sigs = null;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && pi.signingInfo != null) {
                if (pi.signingInfo.hasMultipleSigners()) {
                    sigs = pi.signingInfo.getApkContentsSigners();
                } else {
                    sigs = pi.signingInfo.getSigningCertificateHistory();
                }
            }
            if ((sigs == null || sigs.length == 0) && pi.signatures != null) {
                sigs = pi.signatures;
            }
            if (sigs != null && sigs.length > 0 && sigs[0] != null) {
                java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
                byte[] digest = md.digest(sigs[0].toByteArray());
                StringBuilder sb = new StringBuilder();
                for (byte b : digest) {
                    sb.append(String.format("%02x", b));
                }
                return sb.toString();
            }
        } catch (Exception ignored) {}
        return "";
    }

    private boolean canInstallApksInternalV170() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return getPackageManager().canRequestPackageInstalls();
        }
        return true;
    }

    private void launchApkInstallerIntentV170(final String apkPath) {
        runOnUiThread(() -> {
            try {
                if (apkPath == null || apkPath.isEmpty()) return;
                File apkFile = new File(apkPath);
                if (!apkFile.exists()) {
                    android.widget.Toast.makeText(getApplicationContext(), "❌ APK не найден: " + apkPath, android.widget.Toast.LENGTH_LONG).show();
                    return;
                }
                Uri contentUri = ApkContentProvider.getUriForFile(apkFile);
                Intent intent = new Intent(Intent.ACTION_VIEW);
                intent.setDataAndType(contentUri, "application/vnd.android.package-archive");
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (Exception e) {
                e.printStackTrace();
                android.widget.Toast.makeText(getApplicationContext(), "❌ Ошибка запуска установщика: " + e.getMessage(), android.widget.Toast.LENGTH_LONG).show();
            }
        });
    }

    private void startInstallPermRadarV170() {
        if (installPermWatchRunnableV170 != null) {
            permWatchHandlerV147.removeCallbacks(installPermWatchRunnableV170);
        }
        installPermWatchTicksV170 = 0;
        installPermWatchRunnableV170 = new Runnable() {
            @Override
            public void run() {
                installPermWatchTicksV170++;
                if (canInstallApksInternalV170()) {
                    permWatchHandlerV147.removeCallbacks(this);
                    installPermWatchRunnableV170 = null;
                    try {
                        finishActivity(REQ_INSTALL_PERM);
                    } catch (Exception ignored) {}
                    final String nextApk = pendingInstallApkPathV170;
                    pendingInstallApkPathV170 = null;
                    if (nextApk != null && !nextApk.isEmpty()) {
                        permWatchHandlerV147.postDelayed(() -> launchApkInstallerIntentV170(nextApk), 250);
                    }
                    return;
                }
                if (installPermWatchTicksV170 < 500) {
                    permWatchHandlerV147.postDelayed(this, 150);
                }
            }
        };
        permWatchHandlerV147.postDelayed(installPermWatchRunnableV170, 150);
    }

    private void startUninstallWatchRadarV170() {
        if (uninstallWatchRunnableV170 != null) {
            permWatchHandlerV147.removeCallbacks(uninstallWatchRunnableV170);
        }
        uninstallWatchTicksV170 = 0;
        uninstallWatchRunnableV170 = new Runnable() {
            @Override
            public void run() {
                uninstallWatchTicksV170++;
                String pkg = pendingUninstallPkgV170;
                if (pkg == null || pkg.isEmpty()) return;
                boolean stillInstalled = true;
                try {
                    getPackageManager().getPackageInfo(pkg, 0);
                } catch (PackageManager.NameNotFoundException notFound) {
                    stillInstalled = false;
                } catch (Exception ignored) {}

                if (!stillInstalled) {
                    permWatchHandlerV147.removeCallbacks(this);
                    uninstallWatchRunnableV170 = null;
                    pendingUninstallPkgV170 = null;
                    final String nextApk = pendingInstallApkPathV170;
                    pendingInstallApkPathV170 = null;
                    evalJs("if(window.onUninstallCompletedV170) window.onUninstallCompletedV170();");
                    if (nextApk != null && !nextApk.isEmpty()) {
                        permWatchHandlerV147.postDelayed(() -> launchApkInstallerIntentV170(nextApk), 300);
                    }
                    return;
                }
                if (uninstallWatchTicksV170 < 600) {
                    permWatchHandlerV147.postDelayed(this, 150);
                }
            }
        };
        permWatchHandlerV147.postDelayed(uninstallWatchRunnableV170, 150);
    }

    private boolean hasStoragePermissionInternalV165() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Environment.isExternalStorageManager();
        }
        return ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasNotificationPermissionInternalV165() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, "android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
                return false;
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            try {
                android.app.NotificationManager nm = (android.app.NotificationManager) getSystemService(android.content.Context.NOTIFICATION_SERVICE);
                if (nm != null) {
                    return nm.areNotificationsEnabled();
                }
            } catch (Exception ignored) {}
        }
        return true;
    }

    private void stopStorageGrantRadarV165() {
        if (storageWatchRunnableV165 != null) {
            permWatchHandlerV147.removeCallbacks(storageWatchRunnableV165);
            storageWatchRunnableV165 = null;
        }
    }

    private void startStorageGrantRadarV165() {
        stopStorageGrantRadarV165();
        storageWatchTicksV165 = 0;
        storageWatchRunnableV165 = new Runnable() {
            @Override
            public void run() {
                storageWatchTicksV165++;
                if (hasStoragePermissionInternalV165()) {
                    stopStorageGrantRadarV165();
                    isOpeningSettingsV146 = false;
                    try {
                        finishActivity(REQ_PERM_STORAGE);
                    } catch (Exception ignored) {}
                    evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('storage', true); if(window.fetchData) window.fetchData();");
                    return;
                }
                if (storageWatchTicksV165 < 500) {
                    permWatchHandlerV147.postDelayed(this, 140);
                } else {
                    isOpeningSettingsV146 = false;
                }
            }
        };
        permWatchHandlerV147.postDelayed(storageWatchRunnableV165, 140);
    }

    private void stopNotifGrantRadarV165() {
        if (notifWatchRunnableV165 != null) {
            permWatchHandlerV147.removeCallbacks(notifWatchRunnableV165);
            notifWatchRunnableV165 = null;
        }
    }

    private void startNotifGrantRadarV165() {
        stopNotifGrantRadarV165();
        notifWatchTicksV165 = 0;
        notifWatchRunnableV165 = new Runnable() {
            @Override
            public void run() {
                notifWatchTicksV165++;
                if (hasNotificationPermissionInternalV165()) {
                    stopNotifGrantRadarV165();
                    isOpeningSettingsV146 = false;
                    try {
                        finishActivity(REQ_PERM_NOTIF);
                    } catch (Exception ignored) {}
                    evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('notif', true);");
                    return;
                }
                if (notifWatchTicksV165 < 400) {
                    permWatchHandlerV147.postDelayed(this, 150);
                } else {
                    isOpeningSettingsV146 = false;
                }
            }
        };
        permWatchHandlerV147.postDelayed(notifWatchRunnableV165, 150);
    }

    private void startOverlayGrantRadarV149() {
        stopOverlayPermWatcherV147();
        permWatchTicksV147 = 0;
        permWatchRunnableV147 = new Runnable() {
            @Override
            public void run() {
                permWatchTicksV147++;
                boolean granted = (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) || Settings.canDrawOverlays(MainActivity.this);
                if (granted) {
                    stopOverlayPermWatcherV147();
                    isOpeningSettingsV146 = false;
                    waitingForPermScreenV148 = false;
                    permFlowLockUntilMsV152 = 0L;

                    boolean shouldGoToDesktopOverlay = pendingOverlayAfterGrantV147 && isVideoActiveV132;
                    pendingOverlayAfterGrantV147 = false;

                    try {
                        finishActivity(REQ_OVERLAY_PERM);
                    } catch (Exception ignored) {}

                    if (shouldGoToDesktopOverlay) {
                        // Защищаем созданный оверлей на 3 секунды, пока закрывается окно SubSettings
                        overlayGrantProtectionUntilMsV153 = android.os.SystemClock.uptimeMillis() + 3000L;
                        enterDesktopOverlayInternalV133();
                        evalJs("if(window.ensureVideoPlayingInOverlayV147) window.ensureVideoPlayingInOverlayV147();");
                        try {
                            moveTaskToBack(true);
                        } catch (Exception ignored) {}
                        try {
                            Intent home = new Intent(Intent.ACTION_MAIN);
                            home.addCategory(Intent.CATEGORY_HOME);
                            home.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(home);
                        } catch (Exception ignored) {}
                    } else {
                        // Запрос был открыт сразу при старте видео или со стартового гида — закрываем SubSettings и уведомляем UI!
                        evalJs("if(window.onReturnedFromPermissionScreenV146) window.onReturnedFromPermissionScreenV146(true); if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('overlay', true);");
                    }
                    return;
                }
                if (permWatchTicksV147 < 500) {
                    permWatchHandlerV147.postDelayed(this, 120);
                } else {
                    isOpeningSettingsV146 = false;
                    waitingForPermScreenV148 = false;
                }
            }
        };
        permWatchHandlerV147.postDelayed(permWatchRunnableV147, 140);
    }

    private Intent attachAutoScrollAndSelfCloseFlagsV151(Intent intent, String pkg) {
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
        int appUid = getApplicationInfo().uid;

        Bundle showArgs = new Bundle();
        showArgs.putString("package", pkg);
        showArgs.putInt("uid", appUid);
        showArgs.putString(":settings:show_fragment_title", "Поверх других приложений");
        showArgs.putInt(":android:show_fragment_title", -1);
        showArgs.putString(":settings:fragment_args_key", pkg);
        Intent innerIntent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + pkg));
        showArgs.putParcelable("intent", innerIntent);

        intent.putExtra(":settings:show_fragment", "com.android.settings.applications.appinfo.DrawOverlayDetails");
        intent.putExtra(":settings:show_fragment_args", showArgs);
        intent.putExtra(":settings:show_fragment_title", "Поверх других приложений");
        intent.putExtra(":android:show_fragment_title", -1);
        intent.putExtra(":settings:fragment_args_key", pkg);
        intent.putExtra("package", pkg);
        intent.putExtra("uid", appUid);
        return intent;
    }

    private void openMiuiOrSystemOverlayWindowV148() {
        try {
            String pkg = getPackageName();
            Uri pkgUri = Uri.parse("package:" + pkg);
            boolean launched = false;

            // Шаг 1 (Проверено через Shizuku): Прямой запуск одиночного тумблера DrawOverlayDetails через SubSettings
            // ВАЖНО: запускается строго через startActivityForResult БЕЗ FLAG_ACTIVITY_NEW_TASK, чтобы SubSettings видел callingPackage!
            try {
                Intent subDirect = new Intent(Intent.ACTION_MAIN);
                subDirect.setClassName("com.android.settings", "com.android.settings.SubSettings");
                subDirect.setData(pkgUri);
                attachAutoScrollAndSelfCloseFlagsV151(subDirect, pkg);
                startActivityForResult(subDirect, REQ_OVERLAY_PERM);
                launched = true;
            } catch (Exception ignored) {}

            // Шаг 2: Фоллбэк на стандартный список, если SubSettings недоступен
            if (!launched && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                try {
                    Intent stdList = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, pkgUri);
                    attachAutoScrollAndSelfCloseFlagsV151(stdList, pkg);
                    startActivityForResult(stdList, REQ_OVERLAY_PERM);
                } catch (Exception ignored) {}
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private void launchOverlayPermissionWithAutoWatchV147(final boolean fromHomeButton) {
        runOnUiThread(() -> {
            try {
                long now = android.os.SystemClock.uptimeMillis();
                if (now < permFlowLockUntilMsV152) {
                    return;
                }
                permFlowLockUntilMsV152 = now + 5000L;
                isOpeningSettingsV146 = true;
                pendingOverlayAfterGrantV147 = fromHomeButton;
                subSettingsLaunchedV154 = false;
                evalJs("if(window.saveLiveWorkspaceSnapshotV146) window.saveLiveWorkspaceSnapshotV146(true);");

                try {
                    android.view.Window win = getWindow();
                    if (win != null) {
                        win.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                        win.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
                    }
                } catch (Exception ignored) {}

                android.widget.Toast.makeText(
                    getApplicationContext(),
                    "🔓 Включи тумблер «Поверх других приложений»",
                    android.widget.Toast.LENGTH_SHORT
                ).show();

                if (fromHomeButton) {
                    waitingForPermScreenV148 = true;
                    stopBringBackLoopV155();
                    bringBackTicksV155 = 0;
                    bringBackRunnableV155 = new Runnable() {
                        @Override
                        public void run() {
                            if (!waitingForPermScreenV148 || subSettingsLaunchedV154 || Settings.canDrawOverlays(MainActivity.this)) {
                                stopBringBackLoopV155();
                                return;
                            }
                            bringBackTicksV155++;
                            bringMainActivityToFrontV149();
                            if (bringBackTicksV155 < 28 && waitingForPermScreenV148 && !subSettingsLaunchedV154) {
                                permWatchHandlerV147.postDelayed(this, 180);
                            }
                        }
                    };
                    permWatchHandlerV147.postDelayed(bringBackRunnableV155, 180);
                } else {
                    waitingForPermScreenV148 = false;
                    subSettingsLaunchedV154 = true;
                    startOverlayGrantRadarV149();
                    openMiuiOrSystemOverlayWindowV148();
                }
            } catch (Exception e) {
                isOpeningSettingsV146 = false;
                pendingOverlayAfterGrantV147 = false;
                waitingForPermScreenV148 = false;
                subSettingsLaunchedV154 = false;
                permFlowLockUntilMsV152 = 0L;
                e.printStackTrace();
            }
        });
    }

    @Override
    protected void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (isOpeningSettingsV146 || waitingForPermScreenV148 || android.os.SystemClock.uptimeMillis() < permFlowLockUntilMsV152) {
            return;
        }
        if (isVideoActiveV132 && !isDesktopOverlayActiveV132) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(MainActivity.this)) {
                launchOverlayPermissionWithAutoWatchV147(true);
            } else {
                enterDesktopOverlayInternalV133();
                evalJs("if(window.ensureVideoPlayingInOverlayV147) window.ensureVideoPlayingInOverlayV147();");
            }
        }
    }

    
    private void restoreWebViewToActivityV135(final boolean bringToFront) {
        runOnUiThread(() -> {
            try {
                boolean wasOverlay = isDesktopOverlayActiveV132;
                isDesktopOverlayActiveV132 = false;
                if (!wasOverlay && overlayContainerV132 == null) {
                    if (webView != null) {
                        webView.onResume();
                        webView.resumeTimers();
                    }
                    return;
                }
                if (overlayContainerV132 != null) {
                    overlayContainerV132.removeAllViews();
                    if (wmV132 != null) {
                        try {
                            wmV132.removeViewImmediate(overlayContainerV132);
                        } catch (Exception e1) {
                            try { wmV132.removeView(overlayContainerV132); } catch (Exception ignored) {}
                        }
                    }
                    overlayContainerV132 = null;
                }
                if (webView != null) {
                    webView.setScaleX(1.0f);
                    webView.setScaleY(1.0f);
                    android.view.ViewGroup contentGroup = findViewById(android.R.id.content);
                    android.view.ViewParent parent = webView.getParent();
                    if (parent != contentGroup) {
                        if (parent instanceof android.view.ViewGroup) {
                            ((android.view.ViewGroup) parent).removeView(webView);
                        }
                        setContentView(webView);
                    }
                    webView.onResume();
                    webView.resumeTimers();
                    webView.requestLayout();
                    webView.invalidate();
                }
                if (bringToFront) {
                    Intent i = new Intent(MainActivity.this, MainActivity.class);
                    i.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                    startActivity(i);
                }
                if (wasOverlay) {
                    evalJs("if(window.onDesktopOverlayStateChangedV133) window.onDesktopOverlayStateChangedV133(false);");
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
    }

    public static void evalJs(final String script) {
        if (instance != null && instance.webView != null) {
            instance.webView.post(() -> instance.webView.evaluateJavascript(script, null));
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        instance = this;

        handleIncomingTorrentIntent(getIntent());

        // 1. Сразу регистрируем канал уведомлений при старте!
        // Это мгновенно разблокирует серый тумблер в настройках Android 13-16
        PlaybackService.ensureChannel(this);

        // 2. Разрешения запрашиваются дружелюбно со стартового экрана (Onboarding V165),
        // чтобы не пугать пользователя системными окнами до загрузки интерфейса.

        if (webView != null) {
            if (webView.getParent() instanceof android.view.ViewGroup) {
                ((android.view.ViewGroup) webView.getParent()).removeView(webView);
            }
            webView.setScaleX(1.0f);
                        webView.setScaleY(1.0f);
                        setContentView(webView);
            evalJs("if(window.syncAndroidNotification) window.syncAndroidNotification(true);");
            evalJs("if(window.checkAndroidPendingTorrentV114) window.checkAndroidPendingTorrentV114();");
            return;
        }
        webView = new WebView(getApplicationContext()) {
            @Override
            public void onWindowVisibilityChanged(int visibility) {
                super.onWindowVisibilityChanged(View.VISIBLE);
            }

            // V1.9: WebView создан с контекстом приложения, поэтому система не всегда считает его
            // текстовым редактором и клавиатура не показывается с первого тапа по полю.
            @Override
            public boolean onCheckIsTextEditor() {
                return true;
            }
        };
        webView.setScaleX(1.0f);
                        webView.setScaleY(1.0f);
                        setContentView(webView);

        ensureWebViewFocusV19();

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        webView.clearCache(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback, FileChooserParams fileChooserParams) {
                try {
                    if (filePathCallbackV166 != null) {
                        filePathCallbackV166.onReceiveValue(null);
                    }
                    filePathCallbackV166 = filePathCallback;
                    isOpeningSettingsV146 = true;
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("*/*");
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    startActivityForResult(Intent.createChooser(intent, "Выбери .zip архив"), REQ_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    filePathCallbackV166 = null;
                    return false;
                }
            }
        });

        webView.addJavascriptInterface(new WebAppInterface(), "AndroidBridge");
        getWindow().getDecorView().setOnSystemUiVisibilityChangeListener(visibility -> {
            if ((isVideoFullscreenV132 || isVideoLockedV132) && (visibility & View.SYSTEM_UI_FLAG_HIDE_NAVIGATION) == 0) {
                applyVideoSystemUiLockV132();
            }
        });

        setupFFmpeg();
        checkPermissionsAndStart();
    }

    public class WebAppInterface {
                @JavascriptInterface
        public void setVideoFullscreenLock(boolean fullscreen, boolean locked) {
            isVideoFullscreenV132 = fullscreen;
            isVideoLockedV132 = locked;
            applyVideoSystemUiLockV132();
        }

                @JavascriptInterface
        public String inspectApkFileJson(String apkPath) {
            try {
                JSONObject obj = new JSONObject();
                File f = new File(apkPath != null ? apkPath : "");
                if (!f.exists()) {
                    obj.put("status", "error");
                    obj.put("error", "Файл APK не найден: " + apkPath);
                    return obj.toString();
                }
                PackageManager pm = getPackageManager();
                int flags = PackageManager.GET_SIGNATURES;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    flags |= PackageManager.GET_SIGNING_CERTIFICATES;
                }
                android.content.pm.PackageInfo archiveInfo = pm.getPackageArchiveInfo(apkPath, flags);
                if (archiveInfo == null) {
                    obj.put("status", "error");
                    obj.put("error", "Не удалось прочитать заголовок APK: " + apkPath);
                    return obj.toString();
                }
                String pkg = archiveInfo.packageName;
                String apkVerName = archiveInfo.versionName != null ? archiveInfo.versionName : "";
                long apkVerCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) ? archiveInfo.getLongVersionCode() : archiveInfo.versionCode;
                String apkSig = getSignatureSha256V170(archiveInfo);

                String appName = pkg;
                if (archiveInfo.applicationInfo != null) {
                    archiveInfo.applicationInfo.sourceDir = apkPath;
                    archiveInfo.applicationInfo.publicSourceDir = apkPath;
                    CharSequence lbl = pm.getApplicationLabel(archiveInfo.applicationInfo);
                    if (lbl != null) appName = lbl.toString();
                }

                obj.put("status", "ok");
                obj.put("apkPath", apkPath);
                obj.put("fileName", f.getName());
                obj.put("sizeMb", Math.round((f.length() / (1024.0 * 1024.0)) * 100.0) / 100.0);
                obj.put("package", pkg);
                obj.put("appName", appName);
                obj.put("apkVersionName", apkVerName);
                obj.put("apkVersionCode", apkVerCode);
                obj.put("apkSignature", apkSig);
                obj.put("canInstall", canInstallApksInternalV170());

                try {
                    android.content.pm.PackageInfo instInfo = pm.getPackageInfo(pkg, flags);
                    long instVerCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) ? instInfo.getLongVersionCode() : instInfo.versionCode;
                    String instVerName = instInfo.versionName != null ? instInfo.versionName : "";
                    String instSig = getSignatureSha256V170(instInfo);

                    obj.put("alreadyInstalled", true);
                    obj.put("installedVersionName", instVerName);
                    obj.put("installedVersionCode", instVerCode);
                    obj.put("installedSignature", instSig);
                    boolean sigMismatch = (!apkSig.isEmpty() && !instSig.isEmpty() && !apkSig.equals(instSig));
                    boolean isDowngrade = (apkVerCode < instVerCode);
                    obj.put("signatureMismatch", sigMismatch);
                    obj.put("isDowngrade", isDowngrade);
                } catch (PackageManager.NameNotFoundException notInst) {
                    obj.put("alreadyInstalled", false);
                    obj.put("signatureMismatch", false);
                    obj.put("isDowngrade", false);
                }
                return obj.toString();
            } catch (Exception e) {
                try {
                    JSONObject err = new JSONObject();
                    err.put("status", "error");
                    err.put("error", e.toString());
                    return err.toString();
                } catch (Exception e2) {
                    return "{\"status\":\"error\"}";
                }
            }
        }

        @JavascriptInterface
        public void installApkDirect(final String apkPath) {
            runOnUiThread(() -> {
                try {
                    if (!canInstallApksInternalV170()) {
                        pendingInstallApkPathV170 = apkPath;
                        isOpeningSettingsV146 = true;
                        startInstallPermRadarV170();
                        android.widget.Toast.makeText(
                            getApplicationContext(),
                            "🔓 Разреши «Установку из этого источника» для Jenny Builder!",
                            android.widget.Toast.LENGTH_LONG
                        ).show();
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            Intent intent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName()));
                            startActivityForResult(intent, REQ_INSTALL_PERM);
                        }
                        return;
                    }
                    launchApkInstallerIntentV170(apkPath);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public void uninstallAndInstallApk(final String pkgName, final String apkPath) {
            runOnUiThread(() -> {
                try {
                    if (!canInstallApksInternalV170()) {
                        pendingInstallApkPathV170 = apkPath;
                        isOpeningSettingsV146 = true;
                        startInstallPermRadarV170();
                        android.widget.Toast.makeText(
                            getApplicationContext(),
                            "🔓 Сначала разреши «Установку из этого источника» для Jenny Builder!",
                            android.widget.Toast.LENGTH_LONG
                        ).show();
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            Intent intent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName()));
                            startActivityForResult(intent, REQ_INSTALL_PERM);
                        }
                        return;
                    }
                    pendingUninstallPkgV170 = pkgName;
                    pendingInstallApkPathV170 = apkPath;
                    startUninstallWatchRadarV170();
                    Intent uninstallIntent = new Intent(Intent.ACTION_DELETE, Uri.parse("package:" + pkgName));
                    startActivityForResult(uninstallIntent, REQ_UNINSTALL_PKG);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public String getInstalledAppInfoJson(String pkgName) {
            try {
                android.content.pm.PackageManager pm = getPackageManager();
                android.content.pm.PackageInfo pi = pm.getPackageInfo(pkgName, 0);
                JSONObject obj = new JSONObject();
                obj.put("installed", true);
                obj.put("package", pkgName);
                obj.put("versionName", pi.versionName != null ? pi.versionName : "");
                long vCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) ? pi.getLongVersionCode() : pi.versionCode;
                obj.put("versionCode", vCode);
                obj.put("firstInstallTime", pi.firstInstallTime);
                obj.put("lastUpdateTime", pi.lastUpdateTime);
                if (pi.applicationInfo != null) {
                    obj.put("apkPath", pi.applicationInfo.publicSourceDir != null ? pi.applicationInfo.publicSourceDir : pi.applicationInfo.sourceDir);
                    CharSequence lbl = pm.getApplicationLabel(pi.applicationInfo);
                    obj.put("appName", lbl != null ? lbl.toString() : pkgName);
                }
                return obj.toString();
            } catch (Exception e) {
                try {
                    JSONObject obj = new JSONObject();
                    obj.put("installed", false);
                    obj.put("package", pkgName);
                    return obj.toString();
                } catch (Exception e2) {
                    return "{\"installed\":false}";
                }
            }
        }

        @JavascriptInterface
        public boolean hasStoragePermission() {
            return hasStoragePermissionInternalV165();
        }

        @JavascriptInterface
        public void requestStoragePermission() {
            runOnUiThread(() -> {
                try {
                    if (hasStoragePermissionInternalV165()) {
                        evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('storage', true);");
                        return;
                    }
                    isOpeningSettingsV146 = true;
                    startStorageGrantRadarV165();
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        try {
                            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
                            intent.setData(Uri.fromParts("package", getPackageName(), null));
                            startActivityForResult(intent, REQ_PERM_STORAGE);
                        } catch (Exception e1) {
                            Intent intent2 = new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION);
                            startActivityForResult(intent2, REQ_PERM_STORAGE);
                        }
                    } else {
                        ActivityCompat.requestPermissions(
                            MainActivity.this,
                            new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE, Manifest.permission.READ_EXTERNAL_STORAGE},
                            REQ_PERM_STORAGE
                        );
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public boolean hasNotificationPermission() {
            return hasNotificationPermissionInternalV165();
        }

        @JavascriptInterface
        public void requestNotificationPermission() {
            runOnUiThread(() -> {
                try {
                    PlaybackService.ensureChannel(MainActivity.this);
                    if (hasNotificationPermissionInternalV165()) {
                        evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('notif', true);");
                        return;
                    }
                    startNotifGrantRadarV165();
                    if (Build.VERSION.SDK_INT >= 33) {
                        ActivityCompat.requestPermissions(
                            MainActivity.this,
                            new String[]{"android.permission.POST_NOTIFICATIONS"},
                            REQ_PERM_NOTIF
                        );
                    } else {
                        isOpeningSettingsV146 = true;
                        Intent intent = new Intent();
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            intent.setAction(Settings.ACTION_APP_NOTIFICATION_SETTINGS);
                            intent.putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
                        } else {
                            intent.setAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
                            intent.setData(Uri.fromParts("package", getPackageName(), null));
                        }
                        startActivityForResult(intent, REQ_PERM_NOTIF);
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public boolean canDrawOverlays() {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                return Settings.canDrawOverlays(MainActivity.this);
            }
            return true;
        }

        @JavascriptInterface
        public void requestOverlayPermission() {
            launchOverlayPermissionWithAutoWatchV147(false);
        }

        @JavascriptInterface
        public void setVideoActiveState(boolean active) {
            isVideoActiveV132 = active;
            if (active && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(MainActivity.this)) {
                // Открываем одиночное окно разрешения СРАЗУ при старте видео, пока MainActivity в фокусе на переднем плане!
                // Тогда не нужно свайпать Домой и лишний раз перезаходить в плеер: включил тумблер -> окно само закрылось -> видео играет!
                launchOverlayPermissionWithAutoWatchV147(false);
            }
        }

        @JavascriptInterface
        public void enterDesktopOverlay(final int wDp, final int hDp) {
            enterDesktopOverlayInternalV133();
            runOnUiThread(() -> moveTaskToBack(true));
        }

        @JavascriptInterface
        public void moveDesktopOverlay(final int dxPx, final int dyPx) {
            runOnUiThread(() -> {
                try {
                    if (!isDesktopOverlayActiveV132 || wmV132 == null || overlayContainerV132 == null || overlayParamsV132 == null) return;
                    overlayParamsV132.x += dxPx;
                    overlayParamsV132.y += dyPx;
                    wmV132.updateViewLayout(overlayContainerV132, overlayParamsV132);
                } catch (Exception e) {}
            });
        }

        @JavascriptInterface
        public void resizeDesktopOverlay(final int wPx, final int hPx) {
            runOnUiThread(() -> {
                try {
                    if (!isDesktopOverlayActiveV132 || wmV132 == null || overlayContainerV132 == null || overlayParamsV132 == null) return;
                    overlayParamsV132.width = Math.max(220, Math.min(1400, wPx));
                    overlayParamsV132.height = Math.max(135, Math.min(1800, hPx));
                    wmV132.updateViewLayout(overlayContainerV132, overlayParamsV132);
                } catch (Exception e) {}
            });
        }

        @JavascriptInterface
        public void exitDesktopOverlay(final boolean bringToFront) {
            restoreWebViewToActivityV135(bringToFront);
        }

        @JavascriptInterface
        public String consumePendingTorrent() {
            try {
                if (pendingTorrentPayload == null || pendingTorrentPayload.isEmpty()) {
                    return "";
                }
                JSONObject obj = new JSONObject();
                obj.put("payload", pendingTorrentPayload);
                obj.put("name", pendingTorrentName != null ? pendingTorrentName : "file.torrent");
                pendingTorrentPayload = null;
                pendingTorrentName = null;
                return obj.toString();
            } catch (Exception e) {
                return "";
            }
        }

        @JavascriptInterface
        public void pickTorrentFile() {
            runOnUiThread(() -> {
                try {
                    isOpeningSettingsV146 = true;
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("*/*");
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    startActivityForResult(intent, REQ_PICK_TORRENT);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public void pickFolder() {
            runOnUiThread(() -> {
                try {
                    isOpeningSettingsV146 = true;
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                    startActivityForResult(intent, REQ_PICK_FOLDER);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            });
        }

        @JavascriptInterface
        public void updateMediaState(String title, boolean isPlaying, double posSec, double durSec, double speed) {
            Intent svc = new Intent(MainActivity.this, PlaybackService.class);
            svc.setAction(PlaybackService.ACTION_UPDATE);
            svc.putExtra("title", title);
            svc.putExtra("playing", isPlaying);
            svc.putExtra("pos", (long) (posSec * 1000));
            svc.putExtra("dur", (long) (durSec * 1000));
            svc.putExtra("speed", (float) speed);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(svc);
            } else {
                startService(svc);
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE_CHOOSER) {
            if (filePathCallbackV166 != null) {
                Uri[] results = null;
                if (resultCode == Activity.RESULT_OK && data != null) {
                    String dataString = data.getDataString();
                    android.content.ClipData clipData = data.getClipData();
                    if (clipData != null) {
                        results = new Uri[clipData.getItemCount()];
                        for (int i = 0; i < clipData.getItemCount(); i++) {
                            results[i] = clipData.getItemAt(i).getUri();
                        }
                    } else if (dataString != null) {
                        results = new Uri[]{Uri.parse(dataString)};
                    }
                }
                filePathCallbackV166.onReceiveValue(results);
                filePathCallbackV166 = null;
            }
            return;
        }
        if (requestCode == REQ_PICK_TORRENT && resultCode == Activity.RESULT_OK && data != null) {
            Uri uri = data.getData();
            if (uri != null) {
                readTorrentUriToPending(uri);
                if (pendingTorrentPayload != null && webView != null) {
                    webView.post(() -> evalJs("if(window.checkAndroidPendingTorrentV114) window.checkAndroidPendingTorrentV114();"));
                }
            }
            return;
        }
        if (requestCode == REQ_PICK_FOLDER && resultCode == Activity.RESULT_OK && data != null) {
            Uri treeUri = data.getData();
            if (treeUri != null) {
                String realPath = resolveTreeUriToPath(treeUri);
                if (realPath != null && !realPath.isEmpty()) {
                    String escaped = realPath.replace("\\", "\\\\").replace("'", "\\'");
                    webView.post(() -> webView.evaluateJavascript(
                        "if(window.onNativeFolderPicked) window.onNativeFolderPicked('" + escaped + "');",
                        null
                    ));
                }
            }
        }
    }

    private String resolveTreeUriToPath(Uri treeUri) {
        try {
            String docId = DocumentsContract.getTreeDocumentId(treeUri);
            if (docId == null) return null;
            if (docId.startsWith("raw:")) {
                return docId.substring(4);
            }
            String[] split = docId.split(":");
            String type = split[0];
            String relPath = split.length > 1 ? split[1] : "";

            if ("primary".equalsIgnoreCase(type)) {
                String base = Environment.getExternalStorageDirectory().getAbsolutePath();
                return relPath.isEmpty() ? base : base + "/" + relPath;
            } else {
                String candidate = "/storage/" + type + (relPath.isEmpty() ? "" : "/" + relPath);
                if (new File(candidate).exists()) {
                    return candidate;
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return null;
    }

    private void setupFFmpeg() {
        try {
            File oldBrokenFile = new File(getFilesDir(), "ffmpeg");
            if (oldBrokenFile.exists() && oldBrokenFile.length() < 1000000) {
                oldBrokenFile.delete();
            }

            String nativeDir = getApplicationInfo().nativeLibraryDir;
            File nativeFFmpeg = new File(nativeDir, "libffmpeg.so");
            if (nativeFFmpeg.exists()) {
                nativeFFmpeg.setExecutable(true);
                Os.setenv("FFMPEG_BINARY", nativeFFmpeg.getAbsolutePath(), true);
            }
            File nativeXflac = new File(nativeDir, "libxflac.so");
            if (nativeXflac.exists()) {
                Os.setenv("XFLAC_LIB_PATH", nativeXflac.getAbsolutePath(), true);
            }
            Os.setenv("JENNY_NATIVE_DIR", nativeDir, true);
            Os.setenv("JENNY_FILES_DIR", getFilesDir().getAbsolutePath(), true);

            String path = System.getenv("PATH");
            Os.setenv("PATH", nativeDir + ":" + getFilesDir().getAbsolutePath() + ":" + path, true);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_PERM_STORAGE) {
            boolean ok = hasStoragePermissionInternalV165();
            evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('storage', " + ok + "); if(window.fetchData) window.fetchData();");
        } else if (requestCode == REQ_PERM_NOTIF) {
            boolean ok = hasNotificationPermissionInternalV165();
            if (ok) {
                stopNotifGrantRadarV165();
                evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('notif', true);");
            } else if (!ActivityCompat.shouldShowRequestPermissionRationale(this, "android.permission.POST_NOTIFICATIONS")) {
                // Если Android заблокировал стандартный диалог — открываем системное окно уведомлений нашего приложения
                try {
                    isOpeningSettingsV146 = true;
                    Intent intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS);
                    intent.putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
                    startActivityForResult(intent, REQ_PERM_NOTIF);
                } catch (Exception ignored) {}
            } else {
                evalJs("if(window.onOnboardingPermUpdatedV165) window.onOnboardingPermUpdatedV165('notif', false);");
            }
        }
    }

    private void checkPermissionsAndStart() {
        startPythonServer();
        if (!hasStoragePermissionInternalV165() && !autoAskedStorageOnceV166) {
            autoAskedStorageOnceV166 = true;
            permWatchHandlerV147.postDelayed(() -> {
                if (!hasStoragePermissionInternalV165()) {
                    android.widget.Toast.makeText(
                        getApplicationContext(),
                        "🔓 Включи «Доступ ко всем файлам» для Jenny Builder!",
                        android.widget.Toast.LENGTH_LONG
                    ).show();
                    new WebAppInterface().requestStoragePermission();
                }
            }, 1200);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncomingTorrentIntent(intent);
        evalJs("if(window.syncAndroidNotification) window.syncAndroidNotification(true);");
        evalJs("if(window.checkAndroidPendingTorrentV114) window.checkAndroidPendingTorrentV114();");
    }

    @Override
    protected void onResume() {
        super.onResume();
        boolean granted = (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) || Settings.canDrawOverlays(this);

        if (waitingForPermScreenV148 && !subSettingsLaunchedV154 && !granted && isVideoActiveV132) {
            waitingForPermScreenV148 = false;
            subSettingsLaunchedV154 = true;
            stopBringBackLoopV155();
            isOpeningSettingsV146 = true;
            pendingOverlayAfterGrantV147 = true;
            permFlowLockUntilMsV152 = android.os.SystemClock.uptimeMillis() + 6000L;
            startOverlayGrantRadarV149();
            openMiuiOrSystemOverlayWindowV148();
            try {
                overridePendingTransition(0, 0);
            } catch (Exception ignored) {}
            return;
        }

        if (isDesktopOverlayActiveV132 && android.os.SystemClock.uptimeMillis() < overlayGrantProtectionUntilMsV153) {
            moveTaskToBack(true);
            return;
        }

        stopOverlayPermWatcherV147();
        boolean wasPermSettings = isOpeningSettingsV146;
        boolean wantAutoOverlay = pendingOverlayAfterGrantV147;
        isOpeningSettingsV146 = false;
        pendingOverlayAfterGrantV147 = false;
        waitingForPermScreenV148 = false;
        subSettingsLaunchedV154 = false;
        permFlowLockUntilMsV152 = 0L;

        if (wasPermSettings && wantAutoOverlay && granted && isVideoActiveV132 && !isDesktopOverlayActiveV132) {
            overlayGrantProtectionUntilMsV153 = android.os.SystemClock.uptimeMillis() + 3000L;
            enterDesktopOverlayInternalV133();
            evalJs("if(window.ensureVideoPlayingInOverlayV147) window.ensureVideoPlayingInOverlayV147();");
            moveTaskToBack(true);
            return;
        }

        stopStorageGrantRadarV165();
        stopNotifGrantRadarV165();
        restoreWebViewToActivityV135(false);
        if (webView != null) {
            webView.postDelayed(() -> {
                if (wasPermSettings) {
                    evalJs("if(window.onReturnedFromPermissionScreenV146) window.onReturnedFromPermissionScreenV146(" + granted + ");");
                }
                evalJs("if(window.syncOnboardingPermsV165) window.syncOnboardingPermsV165();");
                if (isVideoFullscreenV132 || isVideoLockedV132) {
                    applyVideoSystemUiLockV132();
                }
            }, 180);
        }
        startPythonServer();
    }

    private void startPythonServer() {
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
            new Thread(() -> {
                Python.getInstance().getModule("player").callAttr("start_server");
            }).start();
            isPageLoaded = true;
            webView.postDelayed(() -> webView.loadUrl("http://127.0.0.1:8795"), 1500);
        } else if (!isPageLoaded) {
            isPageLoaded = true;
            webView.loadUrl("http://127.0.0.1:8795");
        }
    }
}
