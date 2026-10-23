#pragma once
#include <QMainWindow>
#include <QMediaPlayer>
#include <QAudioOutput>
#include <QTimer>
#include <QVector>
#include <QTableWidget>
#include <QLabel>
#include <QPushButton>
#include <QSlider>
#include <QComboBox>
#include <QToolButton>
#include "karaoke.h"
#include "visualizer.h"
#include "miniplayer.h"

struct Track {
    QString path;
    QString title;
    QString artist;
    QString album;
    int num = -1;            // номер трека; -1 = нет (v0.4.8-фикс: «0» — валидный номер)
    qint64 durMs = 0;
    QByteArray cover;        // байты обложки (пусто — нет обложки)
    bool hasLrc = false;
};

// Тема оформления
struct Theme {
    QString name;
    QColor bg, bg2, panel, text, sub, accent, accent2;
};

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(bool selfcheck = false, QWidget *parent = nullptr);
    ~MainWindow() override;

    void startPlaybackFromArgs(const QStringList &paths);

protected:
    void dragEnterEvent(QDragEnterEvent *e) override;
    void dropEvent(QDropEvent *e) override;
    void closeEvent(QCloseEvent *e) override;

private slots:
    // сканирование
    void addFolder();
    void removeFolderMenu();
    void rescanAll();
    void probeStatus(QMediaPlayer::MediaStatus st);
    void probeNext();
    // воспроизведение
    void playIndex(int row);
    void togglePlayPause();
    void nextTrack();
    void prevTrack();
    void positionChanged(qint64 pos);
    void durationChangedMain(qint64 dur);
    void seekSliderMoved();
    void volumeChanged(int v);
    void onPlayerError();
    // интерфейс
    void onCellDouble(int row, int col);
    void onCellClicked(int row, int col);
    void onSortChanged(int col, Qt::SortOrder order);
    void themeSelected(int idx);
    void toggleMini();
    void showWhatsNew();
    void cycleRepeat();
    void toggleShuffle();
    // караоке
    void karaokeSeek(qint64 rawMs);
    void karaokeOffset(qint64 off);

private:
    void buildUi();
    void applyTheme(int idx);
    void refreshStatus();
    void rebuildTable();
    void repopulateTable(const QVector<int> &order);
    void highlightPlayingRow();
    int rowByPath(const QString &path) const;
    void loadKaraoke(const Track &t);
    void updateNowPlayingLabels();
    void saveSettings();
    void loadSettings();
    void restoreLast();
    QString fmtTime(qint64 ms) const;
    int leadingNumber(const QString &fileName) const;
    QPixmap coverPixmap(const Track &t, int side) const;

    // данные
    QVector<Track> m_tracks;      // порядок = порядок строк таблицы
    int m_current = -1;           // индекс в m_tracks
    int m_repeat = 0;             // 0 выкл, 1 один, 2 все
    bool m_shuffle = false;
    bool m_selfcheck = false;

    // плеер
    QMediaPlayer *m_player = nullptr;
    QAudioOutput *m_audio = nullptr;
    QMediaPlayer *m_probe = nullptr;   // сканер метаданных
    QStringList m_scanQueue;
    QString m_probePath;
    QTimer m_probeTimeout;
    bool m_scanActive = false;
    int m_scanDone = 0;

    // UI
    QTableWidget *m_table = nullptr;
    QLabel *m_logo = nullptr;
    QLabel *m_cover = nullptr;
    QLabel *m_lblTitle = nullptr;
    QLabel *m_lblArtist = nullptr;
    QLabel *m_lblTime = nullptr;
    QPushButton *m_btnPrev = nullptr;
    QPushButton *m_btnPlay = nullptr;
    QPushButton *m_btnNext = nullptr;
    QSlider *m_seek = nullptr;
    QSlider *m_volume = nullptr;
    QComboBox *m_themeBox = nullptr;
    QToolButton *m_btnRepeat = nullptr;
    QToolButton *m_btnShuffle = nullptr;
    KaraokeWidget *m_karaoke = nullptr;
    Visualizer *m_viz = nullptr;
    MiniPlayer *m_mini = nullptr;
    QStatusBar *m_status = nullptr;
    bool m_seekPressed = false;
    qint64 m_lastSeekRestore = -1;
};
