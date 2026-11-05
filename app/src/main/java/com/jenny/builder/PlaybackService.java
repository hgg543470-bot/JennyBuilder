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

    private MediaSession mediaSession;
    private PowerManager.WakeLock wakeLock;
    private String currentTitle = "Jenny Player";
    private boolean isPlaying = false;
    private long currentPosMs = 0;
    private long currentDurMs = 0;
    private float currentSpeed = 1.0f;

    public static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "Jenny Player Playback", NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("Медиа-шторка и фоновое воспроизведение");
            channel.setShowBadge(false);
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel(this);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JennyBuilder::AudioWakeLock");
            wakeLock.setReferenceCounted(false);
        }

        mediaSession = new MediaSession(this, "JennyBuilderSession");
        mediaSession.setCallback(new MediaSession.Callback() {
            @Override
            public void onPlay() {
                MainActivity.evalJs("if(window.togglePlay) window.togglePlay();");
            }

            @Override
            public void onPause() {
                MainActivity.evalJs("if(window.togglePlay) window.togglePlay();");
            }

            @Override
            public void onSkipToNext() {
                MainActivity.evalJs("if(window.playNextTrack) window.playNextTrack();");
            }

            @Override
            public void onSkipToPrevious() {
                MainActivity.evalJs("if(window.playPreviousTrack) window.playPreviousTrack();");
            }

            @Override
            public void onSeekTo(long pos) {
                double sec = pos / 1000.0;
                MainActivity.evalJs("if(window.seekFromNotification) window.seekFromNotification(" + sec + ");");
            }
        });
        mediaSession.setActive(true);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.getAction() != null) {
            String act = intent.getAction();
            if (ACTION_PREV.equals(act)) {
                MainActivity.evalJs("if(window.playPreviousTrack) window.playPreviousTrack();");
                return START_STICKY;
            } else if (ACTION_PLAY_PAUSE.equals(act)) {
                MainActivity.evalJs("if(window.togglePlay) window.togglePlay();");
                return START_STICKY;
            } else if (ACTION_NEXT.equals(act)) {
                MainActivity.evalJs("if(window.playNextTrack) window.playNextTrack();");
                return START_STICKY;
            } else if (ACTION_UPDATE.equals(act)) {
                String t = intent.getStringExtra("title");
                if (t != null && !t.isEmpty()) currentTitle = t;
                isPlaying = intent.getBooleanExtra("playing", isPlaying);
                currentPosMs = intent.getLongExtra("pos", currentPosMs);
                currentDurMs = intent.getLongExtra("dur", currentDurMs);
                currentSpeed = intent.getFloatExtra("speed", 1.0f);
            }
        }

        if (wakeLock != null) {
            if (isPlaying && !wakeLock.isHeld()) {
                wakeLock.acquire();
            } else if (!isPlaying && wakeLock.isHeld()) {
                wakeLock.release();
            }
        }

        updateMediaSessionState();
        Notification notification = buildNotification();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(NOTIF_ID, notification);
        }

        return START_STICKY;
    }

    private void updateMediaSessionState() {
        MediaMetadata.Builder metaBuilder = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, currentTitle)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, "🔥 HACKED BY JENNY 🔥")
                .putLong(MediaMetadata.METADATA_KEY_DURATION, currentDurMs > 0 ? currentDurMs : -1L);
        mediaSession.setMetadata(metaBuilder.build());

        long actions = PlaybackState.ACTION_PLAY
                | PlaybackState.ACTION_PAUSE
                | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_SKIP_TO_NEXT
                | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | PlaybackState.ACTION_SEEK_TO;

        int state = isPlaying ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED;
        PlaybackState.Builder stateBuilder = new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, currentPosMs, isPlaying ? currentSpeed : 0f);
        mediaSession.setPlaybackState(stateBuilder.build());
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentPending = PendingIntent.getActivity(
                this, 0, openIntent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent prevPending = PendingIntent.getService(
                this, 1, new Intent(this, PlaybackService.class).setAction(ACTION_PREV),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent playPausePending = PendingIntent.getService(
                this, 2, new Intent(this, PlaybackService.class).setAction(ACTION_PLAY_PAUSE),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        PendingIntent nextPending = PendingIntent.getService(
                this, 3, new Intent(this, PlaybackService.class).setAction(ACTION_NEXT),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        int playPauseIcon = isPlaying ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play;
        String playPauseLabel = isPlaying ? "Pause" : "Play";

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);

        Notification.MediaStyle style = new Notification.MediaStyle()
                .setMediaSession(mediaSession.getSessionToken())
                .setShowActionsInCompactView(0, 1, 2);

        builder.setSmallIcon(android.R.drawable.ic_lock_silent_mode_off)
                .setContentTitle(currentTitle)
                .setContentText("🔥 HACKED BY JENNY 🔥")
                .setContentIntent(contentPending)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(isPlaying)
                .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_previous, "Prev", prevPending).build())
                .addAction(new Notification.Action.Builder(playPauseIcon, playPauseLabel, playPausePending).build())
                .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_next, "Next", nextPending).build())
                .setStyle(style);

        return builder.build();
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
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
