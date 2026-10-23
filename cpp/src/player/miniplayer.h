#pragma once
#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QSlider>

// Мини-плеер: компактное окно поверх всех, обложка, название, кнопки и полоса позиции.
class MiniPlayer : public QWidget {
    Q_OBJECT
public:
    explicit MiniPlayer(QWidget *parent = nullptr);

    void setTrack(const QString &title, const QString &artist, const QPixmap &cover);
    void setPosition(qint64 pos, qint64 dur);
    void setPlaying(bool on);

signals:
    void playPauseRequested();
    void nextRequested();
    void prevRequested();
    void seekPreview(int permille);   // 0..1000 от длительности
    void closeRequested();

protected:
    void paintEvent(QPaintEvent *e) override;

private:
    QLabel *m_cover = nullptr;
    QLabel *m_title = nullptr;
    QLabel *m_artist = nullptr;
    QPushButton *m_prev = nullptr;
    QPushButton *m_play = nullptr;
    QPushButton *m_next = nullptr;
    QSlider *m_pos = nullptr;
};
