#pragma once
#include <QWidget>
#include <QTimer>
#include <QVector>

// Эквалайзер-визуализатор: столбики слева направо, плавная анимация.
// Работает по таймеру, когда плеер играет (setPlaying(true)).
class Visualizer : public QWidget {
    Q_OBJECT
public:
    explicit Visualizer(QWidget *parent = nullptr);
    void setPlaying(bool on);
    void setColors(const QColor &accent, const QColor &accent2, const QColor &bg);

protected:
    void paintEvent(QPaintEvent *event) override;

private:
    QTimer m_timer;
    bool m_playing = false;
    QVector<double> m_levels;   // 0..1 текущие
    QVector<double> m_targets;  // 0..1 цели
    QColor m_accent, m_accent2, m_bg;
};
