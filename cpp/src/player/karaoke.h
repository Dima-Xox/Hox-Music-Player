#pragma once
#include <QWidget>
#include <QVector>
#include <QPair>
#include <QColor>

// Одна строка LRC: время начала, текст, пословные метки (enhanced A2).
struct LrcLine {
    qint64 t = 0;                                       // мс от начала трека
    QString text;                                       // текст строки без тегов
    QVector<QPair<qint64, QString>> words;              // (время мс, слово)
};

// Разбор .lrc (обычные и enhanced A2 строки). Возвращает строки, отсортированные по времени.
QVector<LrcLine> parseLrc(const QString &path);

// Панель караоке: активная строка подсвечивается и заливается слева направо
// (пословно по меткам или линейно), клик по строке — перемотка (с учётом сдвига),
// колесо мыши — ручной сдвиг синхронизации.
class KaraokeWidget : public QWidget {
    Q_OBJECT
public:
    explicit KaraokeWidget(QWidget *parent = nullptr);

    void setLyrics(const QVector<LrcLine> &lines, const QString &trackTitle);
    void clear();
    void setPosition(qint64 posMs);          // «сырая» позиция плеера
    void setOffset(qint64 offsetMs);         // ручной сдвиг (мс)
    qint64 offset() const { return m_offset; }
    bool isEmpty() const { return m_lines.isEmpty(); }
    QString currentLineText() const;         // текст активной строки (для миниплеера)

signals:
    void seekRequested(qint64 posMsRaw);     // перемотка (raw-время строки)
    void offsetChanged(qint64 offsetMs);
    void lineChanged(const QString &text);

protected:
    void paintEvent(QPaintEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void wheelEvent(QWheelEvent *event) override;

private:
    int lineAtY(int y) const;
    void updateCurrent();

    QVector<LrcLine> m_lines;
    qint64 m_pos = 0;      // позиция плеера, мс
    qint64 m_offset = 0;   // ручной сдвиг, мс
    int m_current = -1;
    double m_fill = 0.0;   // 0..1 заливка активной строки
    QString m_title;
};
