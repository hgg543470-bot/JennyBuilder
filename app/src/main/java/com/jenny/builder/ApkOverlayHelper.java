package com.jenny.builder;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.security.MessageDigest;

public class ApkOverlayHelper {
    private static final Handler MAIN_HANDLER = new Handler(Looper.getMainLooper());
    private static View currentOverlayView = null;
    private static View currentMiniBannerView = null;
    private static Runnable uninstallRadarRunnable = null;
    private static int uninstallRadarTicks = 0;
    private static Runnable installPermRadarRunnable = null;
    private static int installPermRadarTicks = 0;

    public static boolean canDrawOverlays(Context ctx) {
        if (ctx == null) return false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(ctx.getApplicationContext());
        }
        return true;
    }

    private static int dp(Context ctx, float val) {
        return Math.round(TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, val, ctx.getResources().getDisplayMetrics()));
    }

    private static GradientDrawable makeBox(int bgColor, int strokeColor, int strokePx, int radiusPx) {
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(bgColor);
        gd.setCornerRadius(radiusPx);
        if (strokePx > 0) {
            gd.setStroke(strokePx, strokeColor);
        }
        return gd;
    }

    private static String getSigSha256(PackageInfo pi) {
        try {
            if (pi == null) return "";
            Signature[] sigs = null;
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
                MessageDigest md = MessageDigest.getInstance("SHA-256");
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

    public static void dismissOverlay(final Context ctx) {
        MAIN_HANDLER.post(() -> dismissOverlayInternal(ctx));
    }

    private static void dismissOverlayInternal(Context ctx) {
        if (currentOverlayView != null && ctx != null) {
            try {
                WindowManager wm = (WindowManager) ctx.getApplicationContext().getSystemService(Context.WINDOW_SERVICE);
                if (wm != null) {
                    wm.removeViewImmediate(currentOverlayView);
                }
            } catch (Exception e1) {
                try {
                    WindowManager wm = (WindowManager) ctx.getApplicationContext().getSystemService(Context.WINDOW_SERVICE);
                    if (wm != null) wm.removeView(currentOverlayView);
                } catch (Exception ignored) {}
            }
            currentOverlayView = null;
        }
    }

    private static void dismissMiniBannerInternal(Context ctx) {
        if (currentMiniBannerView != null && ctx != null) {
            try {
                WindowManager wm = (WindowManager) ctx.getApplicationContext().getSystemService(Context.WINDOW_SERVICE);
                if (wm != null) wm.removeViewImmediate(currentMiniBannerView);
            } catch (Exception e1) {
                try {
                    WindowManager wm = (WindowManager) ctx.getApplicationContext().getSystemService(Context.WINDOW_SERVICE);
                    if (wm != null) wm.removeView(currentMiniBannerView);
                } catch (Exception ignored) {}
            }
            currentMiniBannerView = null;
        }
    }

    private static void showMiniTopBannerInternal(Context ctx, String text) {
        final Context appCtx = ctx.getApplicationContext();
        dismissMiniBannerInternal(appCtx);
        if (!canDrawOverlays(appCtx)) return;
        try {
            WindowManager wm = (WindowManager) appCtx.getSystemService(Context.WINDOW_SERVICE);
            if (wm == null) return;

            TextView tv = new TextView(appCtx);
            tv.setText(text);
            tv.setTextColor(Color.parseColor("#7ee787"));
            tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f);
            tv.setTypeface(Typeface.DEFAULT_BOLD);
            int padH = dp(appCtx, 14);
            int padV = dp(appCtx, 9);
            tv.setPadding(padH, padV, padH, padV);
            tv.setBackground(makeBox(Color.parseColor("#EE0D1117"), Color.parseColor("#3fb950"), dp(appCtx, 1.5f), dp(appCtx, 12)));

            int type = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                    ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                    : WindowManager.LayoutParams.TYPE_PHONE;

            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                    WindowManager.LayoutParams.WRAP_CONTENT,
                    WindowManager.LayoutParams.WRAP_CONTENT,
                    type,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                            | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                    PixelFormat.TRANSLUCENT
            );
            lp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
            lp.y = dp(appCtx, 42);
            wm.addView(tv, lp);
            currentMiniBannerView = tv;
        } catch (Exception ignored) {}
    }

    private static boolean canInstallPackages(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return ctx.getPackageManager().canRequestPackageInstalls();
        }
        return true;
    }

    private static void launchSystemInstallerNow(final Context ctx, final String apkPath) {
        final Context appCtx = ctx.getApplicationContext();
        try {
            File apkFile = new File(apkPath);
            if (!apkFile.exists()) {
                Toast.makeText(appCtx, "❌ APK не найден: " + apkPath, Toast.LENGTH_LONG).show();
                dismissOverlayInternal(appCtx);
                dismissMiniBannerInternal(appCtx);
                return;
            }

            if (!canInstallPackages(appCtx)) {
                showMiniTopBannerInternal(appCtx, "🔓 Разреши установку для Jenny Builder — окно откроется сразу!");
                dismissOverlayInternal(appCtx);
                startInstallPermRadar(appCtx, apkPath);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    Intent permIntent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:" + appCtx.getPackageName()));
                    permIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    appCtx.startActivity(permIntent);
                }
                return;
            }

            Uri contentUri = ApkContentProvider.getUriForFile(apkFile);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(contentUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            // Важно для Android 16 / HyperOS: стартуем установщик ДО снятия оверлея, пока окно активно!
            appCtx.startActivity(intent);
            MAIN_HANDLER.postDelayed(() -> {
                dismissOverlayInternal(appCtx);
                dismissMiniBannerInternal(appCtx);
            }, 220);
        } catch (Exception e) {
            dismissOverlayInternal(appCtx);
            dismissMiniBannerInternal(appCtx);
            Toast.makeText(appCtx, "❌ Ошибка запуска установщика: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private static void startInstallPermRadar(final Context appCtx, final String apkPath) {
        if (installPermRadarRunnable != null) {
            MAIN_HANDLER.removeCallbacks(installPermRadarRunnable);
        }
        installPermRadarTicks = 0;
        installPermRadarRunnable = new Runnable() {
            @Override
            public void run() {
                installPermRadarTicks++;
                if (canInstallPackages(appCtx)) {
                    MAIN_HANDLER.removeCallbacks(this);
                    installPermRadarRunnable = null;
                    launchSystemInstallerNow(appCtx, apkPath);
                    return;
                }
                if (installPermRadarTicks < 400) {
                    MAIN_HANDLER.postDelayed(this, 160);
                } else {
                    dismissMiniBannerInternal(appCtx);
                }
            }
        };
        MAIN_HANDLER.postDelayed(installPermRadarRunnable, 160);
    }

    private static void startUninstallThenInstall(final Context ctx, final String pkgName, final String apkPath) {
        final Context appCtx = ctx.getApplicationContext();
        // Держим компактную верхнюю плашку поверх экрана, чтобы Android 16 / HyperOS разрешил авто-старт установки сразу после удаления!
        showMiniTopBannerInternal(appCtx, "🗑️ Подтверди удаление — установка нового APK стартует мгновенно!");
        try {
            Intent delIntent = new Intent(Intent.ACTION_DELETE, Uri.parse("package:" + pkgName));
            delIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            appCtx.startActivity(delIntent);
        } catch (Exception e) {
            Toast.makeText(appCtx, "❌ Ошибка удаления: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
        MAIN_HANDLER.postDelayed(() -> dismissOverlayInternal(appCtx), 160);

        if (uninstallRadarRunnable != null) {
            MAIN_HANDLER.removeCallbacks(uninstallRadarRunnable);
        }
        uninstallRadarTicks = 0;
        uninstallRadarRunnable = new Runnable() {
            @Override
            public void run() {
                uninstallRadarTicks++;
                boolean stillInstalled = true;
                try {
                    appCtx.getPackageManager().getPackageInfo(pkgName, 0);
                } catch (PackageManager.NameNotFoundException nf) {
                    stillInstalled = false;
                } catch (Exception ignored) {}

                if (!stillInstalled) {
                    MAIN_HANDLER.removeCallbacks(this);
                    uninstallRadarRunnable = null;
                    MainActivity.evalJs("if(window.onUninstallCompletedV170) window.onUninstallCompletedV170();");
                    MAIN_HANDLER.postDelayed(() -> launchSystemInstallerNow(appCtx, apkPath), 200);
                    return;
                }
                if (uninstallRadarTicks < 500) {
                    MAIN_HANDLER.postDelayed(this, 150);
                } else {
                    dismissMiniBannerInternal(appCtx);
                }
            }
        };
        MAIN_HANDLER.postDelayed(uninstallRadarRunnable, 150);
    }

    private static void addInfoRow(Context ctx, LinearLayout parent, String label, String val, int valColor, boolean showDivider) {
        LinearLayout row = new LinearLayout(ctx);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        int pv = dp(ctx, 5);
        row.setPadding(0, pv, 0, pv);

        TextView tvLbl = new TextView(ctx);
        tvLbl.setText(label);
        tvLbl.setTextColor(Color.parseColor("#8b949e"));
        tvLbl.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        LinearLayout.LayoutParams lpLbl = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.38f);
        row.addView(tvLbl, lpLbl);

        TextView tvVal = new TextView(ctx);
        tvVal.setText(val);
        tvVal.setTextColor(valColor);
        tvVal.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        tvVal.setTypeface(Typeface.DEFAULT_BOLD);
        tvVal.setGravity(Gravity.END);
        LinearLayout.LayoutParams lpVal = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.62f);
        row.addView(tvVal, lpVal);

        parent.addView(row);
        if (showDivider) {
            View div = new View(ctx);
            div.setBackgroundColor(Color.parseColor("#21262d"));
            parent.addView(div, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(ctx, 1)));
        }
    }

    private static Button makeStyledBtn(Context ctx, String text, int bgCol, int borderCol, int txtCol) {
        Button b = new Button(ctx);
        b.setAllCaps(false);
        b.setText(text);
        b.setTextColor(txtCol);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setPadding(dp(ctx, 10), dp(ctx, 9), dp(ctx, 10), dp(ctx, 9));
        b.setBackground(makeBox(bgCol, borderCol, dp(ctx, 1.2f), dp(ctx, 9)));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            b.setStateListAnimator(null);
        }
        return b;
    }

    public static boolean showInstallOverlay(final Context ctx, final String apkPath, final String bannerReason) {
        if (ctx == null || apkPath == null || apkPath.isEmpty()) return false;
        final Context appCtx = ctx.getApplicationContext();
        if (!canDrawOverlays(appCtx)) return false;

        final File f = new File(apkPath);
        if (!f.exists()) return false;

        MAIN_HANDLER.post(() -> {
            try {
                dismissOverlayInternal(appCtx);

                PackageManager pm = appCtx.getPackageManager();
                int flags = PackageManager.GET_SIGNATURES;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    flags |= PackageManager.GET_SIGNING_CERTIFICATES;
                }
                PackageInfo archiveInfo = pm.getPackageArchiveInfo(apkPath, flags);
                if (archiveInfo == null) return;

                final String pkg = archiveInfo.packageName;
                String apkVerName = archiveInfo.versionName != null ? archiveInfo.versionName : "—";
                long apkVerCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
                        ? archiveInfo.getLongVersionCode() : archiveInfo.versionCode;
                String apkSig = getSigSha256(archiveInfo);

                String appName = pkg;
                Drawable appIcon = null;
                if (archiveInfo.applicationInfo != null) {
                    archiveInfo.applicationInfo.sourceDir = apkPath;
                    archiveInfo.applicationInfo.publicSourceDir = apkPath;
                    CharSequence lbl = pm.getApplicationLabel(archiveInfo.applicationInfo);
                    if (lbl != null) appName = lbl.toString();
                    try { appIcon = pm.getApplicationIcon(archiveInfo.applicationInfo); } catch (Exception ignored) {}
                }

                boolean alreadyInstalled = false;
                String instVerName = "";
                long instVerCode = 0;
                boolean sigMismatch = false;
                boolean isDowngrade = false;

                try {
                    PackageInfo instInfo = pm.getPackageInfo(pkg, flags);
                    alreadyInstalled = true;
                    instVerName = instInfo.versionName != null ? instInfo.versionName : "—";
                    instVerCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
                            ? instInfo.getLongVersionCode() : instInfo.versionCode;
                    String instSig = getSigSha256(instInfo);
                    sigMismatch = (!apkSig.isEmpty() && !instSig.isEmpty() && !apkSig.equals(instSig));
                    isDowngrade = (apkVerCode < instVerCode);
                } catch (PackageManager.NameNotFoundException ignored) {}

                double sizeMb = Math.round((f.length() / (1024.0 * 1024.0)) * 100.0) / 100.0;
                boolean hasConflict = (sigMismatch || isDowngrade);

                // Полноэкранная подложка с перехватом кнопки «Назад»
                FrameLayout root = new FrameLayout(appCtx) {
                    @Override
                    public boolean dispatchKeyEvent(KeyEvent event) {
                        if (event != null && event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
                            if (event.getAction() == KeyEvent.ACTION_UP) {
                                dismissOverlayInternal(appCtx);
                            }
                            return true;
                        }
                        return super.dispatchKeyEvent(event);
                    }
                };
                root.setBackgroundColor(Color.parseColor("#CC05080D"));
                int outerPad = dp(appCtx, 14);
                root.setPadding(outerPad, outerPad, outerPad, outerPad);

                int borderAccent = hasConflict ? Color.parseColor("#ff7b72") : Color.parseColor("#3fb950");
                LinearLayout card = new LinearLayout(appCtx);
                card.setOrientation(LinearLayout.VERTICAL);
                int cardPad = dp(appCtx, 14);
                card.setPadding(cardPad, cardPad, cardPad, cardPad);
                card.setBackground(makeBox(Color.parseColor("#161b22"), borderAccent, dp(appCtx, 1.8f), dp(appCtx, 15)));

                int maxCardW = Math.min(appCtx.getResources().getDisplayMetrics().widthPixels - dp(appCtx, 24), dp(appCtx, 410));
                FrameLayout.LayoutParams cardLp = new FrameLayout.LayoutParams(maxCardW, ViewGroup.LayoutParams.WRAP_CONTENT);
                cardLp.gravity = Gravity.CENTER;

                // Шапка карточки: иконка + имя приложения + кнопка закрытия
                LinearLayout hdr = new LinearLayout(appCtx);
                hdr.setOrientation(LinearLayout.HORIZONTAL);
                hdr.setGravity(Gravity.CENTER_VERTICAL);

                if (appIcon != null) {
                    ImageView iv = new ImageView(appCtx);
                    iv.setImageDrawable(appIcon);
                    LinearLayout.LayoutParams ivLp = new LinearLayout.LayoutParams(dp(appCtx, 28), dp(appCtx, 28));
                    ivLp.rightMargin = dp(appCtx, 8);
                    hdr.addView(iv, ivLp);
                }

                TextView tvTitle = new TextView(appCtx);
                tvTitle.setText("📲 " + appName);
                tvTitle.setTextColor(Color.parseColor("#7ee787"));
                tvTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f);
                tvTitle.setTypeface(Typeface.DEFAULT_BOLD);
                hdr.addView(tvTitle, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

                Button btnX = makeStyledBtn(appCtx, "✕", Color.parseColor("#21262d"), Color.parseColor("#30363d"), Color.WHITE);
                btnX.setPadding(dp(appCtx, 8), dp(appCtx, 3), dp(appCtx, 8), dp(appCtx, 3));
                btnX.setOnClickListener(v -> dismissOverlayInternal(appCtx));
                hdr.addView(btnX, new LinearLayout.LayoutParams(dp(appCtx, 36), dp(appCtx, 32)));
                card.addView(hdr);

                // Плашка причины (например, авто-скачивание после сборки)
                if (bannerReason != null && !bannerReason.isEmpty()) {
                    TextView tvReason = new TextView(appCtx);
                    tvReason.setText(bannerReason);
                    tvReason.setTextColor(Color.parseColor("#79c0ff"));
                    tvReason.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
                    tvReason.setTypeface(Typeface.DEFAULT_BOLD);
                    int rp = dp(appCtx, 9);
                    tvReason.setPadding(rp, dp(appCtx, 7), rp, dp(appCtx, 7));
                    tvReason.setBackground(makeBox(Color.parseColor("#102131"), Color.parseColor("#58a6ff"), dp(appCtx, 1), dp(appCtx, 9)));
                    LinearLayout.LayoutParams rLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    rLp.topMargin = dp(appCtx, 9);
                    card.addView(tvReason, rLp);
                }

                // Таблица сведений об APK
                LinearLayout infoBox = new LinearLayout(appCtx);
                infoBox.setOrientation(LinearLayout.VERTICAL);
                int ip = dp(appCtx, 10);
                infoBox.setPadding(ip, dp(appCtx, 6), ip, dp(appCtx, 6));
                infoBox.setBackground(makeBox(Color.parseColor("#090d13"), Color.parseColor("#30363d"), dp(appCtx, 1), dp(appCtx, 10)));
                LinearLayout.LayoutParams infoLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                infoLp.topMargin = dp(appCtx, 9);
                infoLp.bottomMargin = dp(appCtx, 9);

                addInfoRow(appCtx, infoBox, "Файл:", f.getName() + " (" + sizeMb + " МБ)", Color.parseColor("#e6edf3"), true);
                addInfoRow(appCtx, infoBox, "Пакет:", pkg, Color.parseColor("#c9d1d9"), true);
                addInfoRow(appCtx, infoBox, "Версия в APK:", apkVerName + " [код: " + apkVerCode + "]", Color.parseColor("#7ee787"), true);

                if (alreadyInstalled) {
                    addInfoRow(appCtx, infoBox, "Установлена:", instVerName + " [код: " + instVerCode + "]", Color.parseColor("#79c0ff"), true);
                    addInfoRow(appCtx, infoBox, "Подпись APK:",
                            sigMismatch ? "❌ Разные подписи!" : "✅ Совпадает",
                            sigMismatch ? Color.parseColor("#ff7b72") : Color.parseColor("#3fb950"),
                            false);
                } else {
                    addInfoRow(appCtx, infoBox, "Статус:", "Новое приложение (не установлено)", Color.parseColor("#e3b341"), false);
                }
                card.addView(infoBox, infoLp);

                // Красный блок авто-удаления при несовпадении подписи или понижении версии
                if (hasConflict) {
                    LinearLayout warnBox = new LinearLayout(appCtx);
                    warnBox.setOrientation(LinearLayout.VERTICAL);
                    int wp = dp(appCtx, 10);
                    warnBox.setPadding(wp, wp, wp, wp);
                    warnBox.setBackground(makeBox(Color.parseColor("#2d1117"), Color.parseColor("#ff7b72"), dp(appCtx, 1.2f), dp(appCtx, 10)));
                    LinearLayout.LayoutParams wLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    wLp.bottomMargin = dp(appCtx, 9);

                    TextView tvWarnH = new TextView(appCtx);
                    tvWarnH.setText(sigMismatch
                            ? "⚠️ Внимание: Подписи APK и приложения разные!"
                            : ("⚠️ Понижение версии (" + apkVerCode + " поверх " + instVerCode + ")!"));
                    tvWarnH.setTextColor(Color.parseColor("#ffa198"));
                    tvWarnH.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f);
                    tvWarnH.setTypeface(Typeface.DEFAULT_BOLD);
                    warnBox.addView(tvWarnH);

                    TextView tvWarnD = new TextView(appCtx);
                    tvWarnD.setText("Удалить установленную версию и сразу автоматически открыть установку этого APK?");
                    tvWarnD.setTextColor(Color.parseColor("#e6edf3"));
                    tvWarnD.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
                    LinearLayout.LayoutParams wdLp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    wdLp.topMargin = dp(appCtx, 4);
                    wdLp.bottomMargin = dp(appCtx, 8);
                    warnBox.addView(tvWarnD, wdLp);

                    Button btnUninst = makeStyledBtn(appCtx,
                            "🗑️ ОК: Удалить старую и сразу установить",
                            Color.parseColor("#5a1e24"), Color.parseColor("#ff7b72"), Color.parseColor("#ffa198"));
                    btnUninst.setOnClickListener(v -> startUninstallThenInstall(appCtx, pkg, apkPath));
                    warnBox.addView(btnUninst, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

                    card.addView(warnBox, wLp);
                }

                // Нижний ряд кнопок: Установить APK | Позже
                LinearLayout btnRow = new LinearLayout(appCtx);
                btnRow.setOrientation(LinearLayout.HORIZONTAL);

                Button btnInst = makeStyledBtn(appCtx, "📲 Установить APK",
                        Color.parseColor("#1f4729"), Color.parseColor("#3fb950"), Color.parseColor("#7ee787"));
                btnInst.setOnClickListener(v -> launchSystemInstallerNow(appCtx, apkPath));
                LinearLayout.LayoutParams b1Lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.62f);
                b1Lp.rightMargin = dp(appCtx, 7);
                btnRow.addView(btnInst, b1Lp);

                Button btnClose = makeStyledBtn(appCtx, "Позже",
                        Color.parseColor("#21262d"), Color.parseColor("#30363d"), Color.parseColor("#c9d1d9"));
                btnClose.setOnClickListener(v -> dismissOverlayInternal(appCtx));
                LinearLayout.LayoutParams b2Lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.38f);
                btnRow.addView(btnClose, b2Lp);

                card.addView(btnRow, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

                ScrollView sv = new ScrollView(appCtx);
                sv.addView(card);
                root.addView(sv, cardLp);

                card.setScaleX(0.92f);
                card.setScaleY(0.92f);
                card.setAlpha(0f);
                card.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(150).start();

                int type = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                        ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_PHONE;

                WindowManager.LayoutParams wlp = new WindowManager.LayoutParams(
                        WindowManager.LayoutParams.MATCH_PARENT,
                        WindowManager.LayoutParams.MATCH_PARENT,
                        type,
                        WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM
                                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                                | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                        PixelFormat.TRANSLUCENT
                );
                wlp.gravity = Gravity.CENTER;

                WindowManager wm = (WindowManager) appCtx.getSystemService(Context.WINDOW_SERVICE);
                if (wm != null) {
                    wm.addView(root, wlp);
                    currentOverlayView = root;
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
        return true;
    }
}
