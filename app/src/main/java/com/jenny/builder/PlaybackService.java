package com.jenny.builder;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

public class PlaybackService extends Service {
    public static final String CHANNEL_ID = "jenny_media_channel";
    private static final int NOTIF_ID = 101;

    public static final String ACTION_UPDATE = "com.jenny.builder.ACTION_UPDATE";
    public static final String ACTION_PREV = "com.jenny.builder.ACTION_PREV";
    public static final String ACTION_PLAY_PAUSE = "com.jenny.builder.ACTION_PLAY_PAUSE";
    public static final String ACTION_NEXT = "com.jenny.builder.ACTION_NEXT";
    public static final String ACTION_BG_TASK = "com.jenny.builder.ACTION_BG_TASK";

    private MediaSession mediaSession;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private String currentTitle = "Jenny Builder";
    private boolean isPlaying = false;
    private long currentPosMs = 0;
    private long currentDurMs = 0;
    private float currentSpeed = 1.0f;

    public static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "Jenny Builder Background Tasks", NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("Фоновая загрузка APK и отправка сборок на GitHub без засыпания");
            channel.setShowBadge(false);
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    public static void updateBgTaskGuard(Context ctx, boolean active, String title, String detail) {
        if (ctx == null) return;
        try {
            Context appCtx = ctx.getApplicationContext();
            if (active) {
                Intent svc = new Intent(appCtx, PlaybackService.class);
                svc.setAction(ACTION_BG_TASK);
                svc.putExtra("bg_title", title != null ? title : "Jenny Builder");
                svc.putExtra("bg_detail", detail != null ? detail : "Работа в фоне...");
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    appCtx.startForegroundService(svc);
                } else {
                    appCtx.startService(svc);
                }
            } else {
                Intent svc = new Intent(appCtx, PlaybackService.class);
                appCtx.stopService(svc);
            }
        } catch (Exception ignored) {}
    }

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel(this);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JennyBuilder::BgWakeLock");
            wakeLock.setReferenceCounted(false);
        }

        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                int lockMode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                        ? WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                        : WifiManager.WIFI_MODE_FULL_HIGH_PERF;
                wifiLock = wm.createWifiLock(lockMode, "JennyBuilder::BgWifiLock");
                wifiLock.setReferenceCounted(false);
            }
        } catch (Exception ignored) {}

        mediaSession = new MediaSession(this, "JennyBuilderSession");
        mediaSession.setActive(true);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_BG_TASK.equals(intent.getAction())) {
            String bt = intent.getStringExtra("bg_title");
            String bd = intent.getStringExtra("bg_detail");
            try {
                if (wakeLock != null && !wakeLock.isHeld()) {
                    wakeLock.acquire(45 * 60 * 1000L);
                }
            } catch (Exception ignored) {}
            try {
                if (wifiLock != null && !wifiLock.isHeld()) {
                    wifiLock.acquire();
                }
            } catch (Exception ignored) {}

            Notification notif = buildBgTaskNotification(
                    (bt != null && !bt.isEmpty()) ? bt : "⚡ Jenny Builder",
                    (bd != null && !bd.isEmpty()) ? bd : "Выполнение операции в фоне..."
            );
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(NOTIF_ID, notif);
            }
            return START_STICKY;
        }

        if (intent != null && intent.getAction() != null) {
            String act = intent.getAction();
            if (ACTION_UPDATE.equals(act)) {
                String t = intent.getStringExtra("title");
                if (t != null && !t.isEmpty()) currentTitle = t;
                isPlaying = intent.getBooleanExtra("playing", isPlaying);
                currentPosMs = intent.getLongExtra("pos", currentPosMs);
                currentDurMs = intent.getLongExtra("dur", currentDurMs);
                currentSpeed = intent.getFloatExtra("speed", 1.0f);
            }
        }

        if (wakeLock != null) {
            if (isPlaying && !wakeLock.isHeld()) wakeLock.acquire();
            else if (!isPlaying && wakeLock.isHeld()) wakeLock.release();
        }

        Notification notification = buildBgTaskNotification(currentTitle, "Фоновый сервис активен");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(NOTIF_ID, notification);
        }
        return START_STICKY;
    }

    private Notification buildBgTaskNotification(String title, String detail) {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentPending = PendingIntent.getActivity(
                this, 0, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);

        builder.setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle(title)
                .setContentText(detail)
                .setContentIntent(contentPending)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setOnlyAlertOnce(true);

        return builder.build();
    }

    @Override
    public void onDestroy() {
        try { if (wakeLock != null && wakeLock.isHeld()) wakeLock.release(); } catch (Exception ignored) {}
        try { if (wifiLock != null && wifiLock.isHeld()) wifiLock.release(); } catch (Exception ignored) {}
        if (mediaSession != null) {
            mediaSession.setActive(false);
            mediaSession.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
