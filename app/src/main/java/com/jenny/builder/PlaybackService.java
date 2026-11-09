package com.jenny.builder;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import com.chaquo.python.Python;

public class PlaybackService extends Service {
    public static final String CHANNEL_ID = "jenny_builder_shade_v23";
    private static final int NOTIF_ID = 101;

    public static final String ACTION_UPDATE = "com.jenny.builder.ACTION_UPDATE";
    public static final String ACTION_BG_TASK = "com.jenny.builder.ACTION_BG_TASK";
    public static final String ACTION_STATUS_SYNC = "com.jenny.builder.ACTION_STATUS_SYNC";
    public static final String ACTION_CANCEL_DL = "com.jenny.builder.ACTION_CANCEL_DL";
    public static final String ACTION_REFRESH_RUNS = "com.jenny.builder.ACTION_REFRESH_RUNS";
    public static final String ACTION_DISMISS_SHADE = "com.jenny.builder.ACTION_DISMISS_SHADE";

    private static volatile boolean shadeDismissedByUser = false;
    private static volatile boolean lastTaskActive = false;
    private static volatile int lastPercent = 0;
    private static volatile String lastTaskTitle = "";
    private static volatile String lastTaskDetail = "";
    private static volatile boolean lastCancellable = false;
    private static volatile String lastStatusLine = "🟢 Готов к работе.";
    private static volatile boolean lastStatusErr = false;

    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    public static void ensureChannel(Context ctx) {
        if (ctx == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Jenny Builder — Шторка состояния и закачек",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Показывает процент закачек, сборку GitHub и живую строку состояния");
            channel.setShowBadge(false);
            channel.enableVibration(false);
            channel.setSound(null, null);
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    public static void updateBgTaskGuard(Context ctx, boolean active, int percent, String title, String detail, boolean cancellable) {
        if (ctx == null) return;
        try {
            if (active) shadeDismissedByUser = false;
            lastTaskActive = active;
            lastPercent = Math.max(0, Math.min(100, percent));
            lastTaskTitle = title != null ? title : "";
            lastTaskDetail = detail != null ? detail : "";
            lastCancellable = cancellable;

            if (!active && shadeDismissedByUser) {
                return;
            }
            Context appCtx = ctx.getApplicationContext();
            Intent svc = new Intent(appCtx, PlaybackService.class);
            svc.setAction(ACTION_BG_TASK);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                appCtx.startForegroundService(svc);
            } else {
                appCtx.startService(svc);
            }
        } catch (Exception ignored) {}
    }

    public static void updateBgTaskGuard(Context ctx, boolean active, String title, String detail) {
        updateBgTaskGuard(ctx, active, active ? 50 : 100, title, detail, false);
    }

    public static void syncStatusText(Context ctx, String statusText, boolean isErr) {
        if (ctx == null || statusText == null || statusText.trim().isEmpty()) return;
        try {
            lastStatusLine = statusText.trim();
            lastStatusErr = isErr;
            if (shadeDismissedByUser && !lastTaskActive) return;

            Context appCtx = ctx.getApplicationContext();
            Intent svc = new Intent(appCtx, PlaybackService.class);
            svc.setAction(ACTION_STATUS_SYNC);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                appCtx.startForegroundService(svc);
            } else {
                appCtx.startService(svc);
            }
        } catch (Exception ignored) {}
    }

    public static void setShadeVisible(Context ctx, boolean visible) {
        if (ctx == null) return;
        try {
            shadeDismissedByUser = !visible;
            Context appCtx = ctx.getApplicationContext();
            Intent svc = new Intent(appCtx, PlaybackService.class);
            if (visible) {
                svc.setAction(ACTION_STATUS_SYNC);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    appCtx.startForegroundService(svc);
                } else {
                    appCtx.startService(svc);
                }
            } else {
                svc.setAction(ACTION_DISMISS_SHADE);
                appCtx.startService(svc);
            }
        } catch (Exception ignored) {}
    }

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel(this);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JennyBuilder::DataSyncWakeLock");
            wakeLock.setReferenceCounted(false);
        }

        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                int lockMode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                        ? WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                        : WifiManager.WIFI_MODE_FULL_HIGH_PERF;
                wifiLock = wm.createWifiLock(lockMode, "JennyBuilder::DataSyncWifiLock");
                wifiLock.setReferenceCounted(false);
            }
        } catch (Exception ignored) {}
    }

    private void applyLocks(boolean hold) {
        try {
            if (wakeLock != null) {
                if (hold && !wakeLock.isHeld()) {
                    wakeLock.acquire(60 * 60 * 1000L);
                } else if (!hold && wakeLock.isHeld()) {
                    wakeLock.release();
                }
            }
        } catch (Exception ignored) {}
        try {
            if (wifiLock != null) {
                if (hold && !wifiLock.isHeld()) {
                    wifiLock.acquire();
                } else if (!hold && wifiLock.isHeld()) {
                    wifiLock.release();
                }
            }
        } catch (Exception ignored) {}
    }

    private void pushForegroundSafe(Notification notif) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIF_ID, notif);
            }
        } catch (Exception e) {
            try {
                NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
                if (nm != null) nm.notify(NOTIF_ID, notif);
            } catch (Exception ignored) {}
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String act = (intent != null && intent.getAction() != null) ? intent.getAction() : ACTION_STATUS_SYNC;

        if (ACTION_DISMISS_SHADE.equals(act)) {
            shadeDismissedByUser = true;
            lastTaskActive = false;
            applyLocks(false);
            try { stopForeground(true); } catch (Exception ignored) {}
            stopSelf();
            return START_NOT_STICKY;
        }

        if (ACTION_CANCEL_DL.equals(act)) {
            lastTaskActive = false;
            lastCancellable = false;
            lastStatusLine = "⏹ Загрузка отменена из шторки уведомлений.";
            applyLocks(false);
            new Thread(() -> {
                try {
                    if (Python.isStarted()) {
                        Python.getInstance().getModule("player").callAttr("trigger_download_cancel");
                    }
                } catch (Exception ignored) {}
            }).start();
            MainActivity.evalJs("if(window.onShadeCancelTriggeredV23) window.onShadeCancelTriggeredV23();");
            pushForegroundSafe(buildShadeNotification());
            return START_STICKY;
        }

        if (ACTION_REFRESH_RUNS.equals(act)) {
            shadeDismissedByUser = false;
            lastStatusLine = "🔄 Обновление списка сборок с GitHub...";
            pushForegroundSafe(buildShadeNotification());
            MainActivity.evalJs("if(window.loadRuns) window.loadRuns(true);");
            new Thread(() -> {
                try {
                    if (Python.isStarted()) {
                        Python.getInstance().getModule("player").callAttr("refresh_shade_summary_from_gh");
                    }
                } catch (Exception ignored) {}
            }).start();
            return START_STICKY;
        }

        applyLocks(lastTaskActive);
        pushForegroundSafe(buildShadeNotification());
        return START_STICKY;
    }

    private Notification buildShadeNotification() {
        ensureChannel(this);

        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentPending = PendingIntent.getActivity(
                this, 10, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent cancelPending = PendingIntent.getService(
                this, 11, new Intent(this, PlaybackService.class).setAction(ACTION_CANCEL_DL),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent refreshPending = PendingIntent.getService(
                this, 12, new Intent(this, PlaybackService.class).setAction(ACTION_REFRESH_RUNS),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent dismissPending = PendingIntent.getService(
                this, 13, new Intent(this, PlaybackService.class).setAction(ACTION_DISMISS_SHADE),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }

        if (lastTaskActive) {
            String cleanTitle = (lastTaskTitle != null && !lastTaskTitle.isEmpty()) ? lastTaskTitle : "⚡ Выполнение";
            String head = cleanTitle + " • " + lastPercent + "%";
            String detail = (lastTaskDetail != null && !lastTaskDetail.isEmpty()) ? lastTaskDetail : "Работа в фоновом режиме DataSync...";
            String expanded = detail + "\n────────────────────\n📟 Состояние:\n" + lastStatusLine;

            builder.setSmallIcon(android.R.drawable.stat_sys_download)
                    .setContentTitle(head)
                    .setContentText(detail)
                    .setStyle(new Notification.BigTextStyle().bigText(expanded).setBigContentTitle(head))
                    .setProgress(100, lastPercent, lastPercent <= 1)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setVisibility(Notification.VISIBILITY_PUBLIC)
                    .setContentIntent(contentPending);

            if (lastCancellable) {
                builder.addAction(new Notification.Action.Builder(
                        android.R.drawable.ic_menu_close_clear_cancel, "⏹ Отмена", cancelPending).build());
            }
            builder.addAction(new Notification.Action.Builder(
                    android.R.drawable.ic_menu_view, "📂 Открыть", contentPending).build());
        } else {
            String[] lines = lastStatusLine.split("\n");
            String firstLine = lines.length > 0 ? lines[0].trim() : "🟢 Готов к работе";
            String secondLine = lines.length > 1 ? lines[1].trim() : firstLine;
            String title = lastStatusErr ? "⚠️ Jenny Builder — Внимание" : ("🛠️ " + (firstLine.length() > 48 ? firstLine.substring(0, 48) + "…" : firstLine));

            builder.setSmallIcon(lastStatusErr ? android.R.drawable.stat_notify_error : android.R.drawable.stat_notify_sync_noanim)
                    .setContentTitle(title)
                    .setContentText(secondLine)
                    .setStyle(new Notification.BigTextStyle().bigText(lastStatusLine).setBigContentTitle("📟 Jenny Builder — Строка состояния"))
                    .setProgress(0, 0, false)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setVisibility(Notification.VISIBILITY_PUBLIC)
                    .setContentIntent(contentPending);

            builder.addAction(new Notification.Action.Builder(
                    android.R.drawable.ic_popup_sync, "🔄 Обновить", refreshPending).build());
            builder.addAction(new Notification.Action.Builder(
                    android.R.drawable.ic_menu_close_clear_cancel, "🔕 Скрыть", dismissPending).build());
        }

        return builder.build();
    }

    @Override
    public void onDestroy() {
        applyLocks(false);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
