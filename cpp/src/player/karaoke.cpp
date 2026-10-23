#include "karaoke.h"
#include <QPainter>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QRegularExpression>
#include <QFontMetrics>
#include <QFile>
#include <QtMath>
#ifdef Q_OS_WIN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include <QVarLengthArray>
#include <algorithm>

QVector<LrcLine> parseLrc(const QString &path)
{
    QVector<LrcLine> out;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
        return out;
    const QByteArray raw = f.readAll();

    // Определяем кодировку: UTF-8/UTF-16 с BOM, иначе пробуем UTF-8, потом CP1251
    QString content;
    if (raw.size() >= 2 && ((quint8)raw[0] == 0xFF && (quint8)raw[1] == 0xFE)) {
        content = QString::fromUtf16(reinterpret_cast<const char16_t *>(raw.constData() + 2),
                                     (raw.size() - 2) / 2);
    } else if (raw.size() >= 2 && ((quint8)raw[0] == 0xFE && (quint8)raw[1] == 0xFF)) {
        // UTF-16 BE — редко; конвертируем перестановкой байт
        QByteArray sw = raw.mid(2);
        for (int i = 0; i + 1 < sw.size(); i += 2)
            std::swap(sw[i], sw[i + 1]);
        content = QString::fromUtf16(reinterpret_cast<const char16_t *>(sw.constData()), sw.size() / 2);
    } else {
        content = QString::fromUtf8(raw);
        if (content.contains(QChar(0xFFFD))) {
            // не UTF-8 — пробуем CP1251 (наши файлы чаще всего в ней)
#ifdef Q_OS_WIN
            if (int len = MultiByteToWideChar(1251, 0, raw.constData(), raw.size(), nullptr, 0); len > 0) {
                QVarLengthArray<wchar_t, 8192> buf(len);
                MultiByteToWideChar(1251, 0, raw.constData(), raw.size(), buf.data(), len);
                content = QString::fromWCharArray(buf.data(), len);
            }
#else
            content = QString::fromLatin1(raw);
#endif
        }
    }

    static const QRegularExpression tagRe(
        R"(\[(\d+):(\d+(?:\.\d+)?)\])");
    static const QRegularExpression wordRe(
        R"(<(\d+):(\d+(?:\.\d+)?)>)");

    const QStringList lines = content.split(QLatin1Char('\n'));
    for (const QString &rawLine : lines) {
        QString line = rawLine.trimmed();
        if (line.isEmpty())
            continue;
        // собираем все временные теги в начале строки
        QList<qint64> times;
        qsizetype pos = 0;
        while (pos < line.size()) {
            auto m = tagRe.match(line, pos, QRegularExpression::PartialPreferCompleteMatch);
            if (!m.hasMatch() || m.capturedStart() != pos)
                break;
            qint64 t = qint64(m.captured(1).toInt()) * 60000 + qint64(m.captured(2).toDouble() * 1000.0);
            times.append(t);
            pos = m.capturedEnd();
        }
        if (times.isEmpty())
            continue;   // метаданные [ar:] и прочее — пропускаем
        QString text = line.mid(pos).trimmed();

        // пословные метки <mm:ss.xx>
        QVector<QPair<qint64, QString>> words;
        struct Seg { qint64 t; int start; int end; };
        QVector<Seg> segs;
        {
            auto it = wordRe.globalMatch(text);
            while (it.hasNext()) {
                auto m = it.next();
                segs.append({ qint64(m.captured(1).toInt()) * 60000
                                  + qint64(m.captured(2).toDouble() * 1000.0),
                              int(m.capturedStart()), int(m.capturedEnd()) });
            }
        }
        if (!segs.isEmpty()) {
            // слово i живёт от конца метки i до начала метки i+1 (или до конца строки)
            for (int i = 0; i < segs.size(); ++i) {
                const int wstart = segs[i].end;
                const int wend = (i + 1 < segs.size()) ? segs[i + 1].start : int(text.size());
                if (wend >= wstart)
                    words.append({ segs[i].t, text.mid(wstart, wend - wstart) });
            }
        }
        QString plain = text;
        plain.remove(wordRe);

        for (qint64 t : times) {
            LrcLine l;
            l.t = t;
            l.text = plain;
            l.words = words;
            out.append(l);
        }
    }
    std::sort(out.begin(), out.end(), [](const LrcLine &a, const LrcLine &b) { return a.t < b.t; });
    return out;
}

KaraokeWidget::KaraokeWidget(QWidget *parent)
    : QWidget(parent)
{
    setMinimumHeight(120);
    setMouseTracking(false);
}

void KaraokeWidget::setLyrics(const QVector<LrcLine> &lines, const QString &trackTitle)
{
    m_lines = lines;
    m_title = trackTitle;
    m_current = -1;
    m_fill = 0.0;
    m_pos = 0;
    setVisible(!m_lines.isEmpty());
    update();
}

void KaraokeWidget::clear()
{
    m_lines.clear();
    m_current = -1;
    m_fill = 0.0;
    setVisible(false);
    emit lineChanged(QString());
    update();
}

void KaraokeWidget::setOffset(qint64 offsetMs)
{
    m_offset = offsetMs;
    updateCurrent();
    update();
}

void KaraokeWidget::setPosition(qint64 posMs)
{
    m_pos = posMs;
    updateCurrent();
    update();
}

void KaraokeWidget::updateCurrent()
{
    const qint64 t = m_pos + m_offset;
    int cur = -1;
    for (int i = 0; i < m_lines.size(); ++i) {
        if (m_lines[i].t <= t)
            cur = i;
        else
            break;
    }
    double fill = 0.0;
    if (cur >= 0) {
        const LrcLine &l = m_lines[cur];
        if (!l.words.isEmpty()) {
            // пословная заливка
            int widx = -1;
            for (int i = 0; i < l.words.size(); ++i) {
                if (l.words[i].first <= t)
                    widx = i;
                else
                    break;
            }
            if (widx < 0) {
                fill = 0.0;
            } else if (widx + 1 < l.words.size()) {
                const qint64 t0 = l.words[widx].first;
                const qint64 t1 = l.words[widx + 1].first;
                fill = (qint64(widx) + (t1 > t0 ? double(t - t0) / double(t1 - t0) : 1.0))
                       / double(l.words.size());
            } else {
                qint64 next = (cur + 1 < m_lines.size()) ? m_lines[cur + 1].t : l.t + 6000;
                const qint64 t0 = l.words[widx].first;
                fill = (qint64(widx) + (next > t0 ? double(t - t0) / double(next - t0) : 1.0))
                       / double(l.words.size());
            }
        } else {
            // линейная заливка по длине строки
            qint64 next = (cur + 1 < m_lines.size()) ? m_lines[cur + 1].t : l.t + 6000;
            fill = (next > l.t) ? double(t - l.t) / double(next - l.t) : 0.0;
        }
    }
    fill = qBound(0.0, fill, 1.0);
    if (cur != m_current || qAbs(fill - m_fill) > 0.002) {
        m_current = cur;
        m_fill = fill;
        emit lineChanged(currentLineText());
    }
}

QString KaraokeWidget::currentLineText() const
{
    if (m_current < 0 || m_current >= m_lines.size())
        return QString();
    return m_lines[m_current].text;
}

int KaraokeWidget::lineAtY(int y) const
{
    if (m_lines.isEmpty())
        return -1;
    const int lh = 32;
    const int center = height() / 2;
    const int r = (y - center) / lh;   // отрицательные — выше активной
    int idx = m_current + r;
    if (idx < 0 || idx >= m_lines.size())
        return -1;
    return idx;
}

void KaraokeWidget::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    const QColor bg = palette().color(QPalette::Window);
    const QColor txt = palette().color(QPalette::WindowText);
    const QColor sub = QColor(txt.red(), txt.green(), txt.blue(), 120);
    const QColor accent = palette().color(QPalette::Highlight);

    p.fillRect(rect(), bg);
    if (m_lines.isEmpty())
        return;

    const int lh = 32;
    const int center = height() / 2;
    QFont f = font();
    f.setPointSize(11);
    f.setBold(true);
    p.setFont(f);
    const QFontMetrics fm(f);

    for (int i = 0; i < m_lines.size(); ++i) {
        int dy = (i - m_current) * lh;
        int y = center + dy - lh / 2 + 8;
        if (y < -lh || y > height() + lh)
            continue;
        if (i == m_current) {
            // активная строка: заливка слева направо
            const QString text = m_lines[i].text;
            const int x0 = 24;
            const int w = width() - 48;
            p.setPen(sub);
            p.drawText(QRect(x0, y, w, lh), Qt::AlignVCenter | Qt::AlignHCenter, text);
            // заливка цветом темы
            const int fw = int(w * m_fill);
            if (fw > 0) {
                p.setClipRect(QRect(x0, y, fw, lh));
                p.setPen(accent);
                p.drawText(QRect(x0, y, w, lh), Qt::AlignVCenter | Qt::AlignHCenter, text);
                p.setClipping(false);
            }
        } else {
            QColor c = (i < m_current) ? QColor(txt.red(), txt.green(), txt.blue(), 70) : sub;
            p.setPen(c);
            p.drawText(QRect(24, y, width() - 48, lh), Qt::AlignVCenter | Qt::AlignHCenter,
                       fm.elidedText(m_lines[i].text, Qt::ElideRight, width() - 48));
        }
    }
}

void KaraokeWidget::mousePressEvent(QMouseEvent *e)
{
    int idx = lineAtY(int(e->position().y()));
    if (idx >= 0 && idx < m_lines.size()) {
        // v0.4.8-фикс: учитываем ручной сдвиг синхронизации — перемотка ставит
        // «голое» время строки БЕЗ сдвига (позиция плеера живёт без него)
        emit seekRequested(m_lines[idx].t);
    }
}

void KaraokeWidget::wheelEvent(QWheelEvent *e)
{
    const qint64 step = (e->angleDelta().y() > 0) ? 100 : -100;
    m_offset += step;
    emit offsetChanged(m_offset);
    updateCurrent();
    update();
}
