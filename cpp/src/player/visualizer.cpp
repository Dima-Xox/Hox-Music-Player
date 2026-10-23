#include "visualizer.h"
#include <QPainter>
#include <QRandomGenerator>
#include <QtMath>

Visualizer::Visualizer(QWidget *parent)
    : QWidget(parent)
{
    setMinimumHeight(56);
    m_levels.resize(28);
    m_targets.resize(28);
    for (int i = 0; i < m_levels.size(); ++i) {
        m_levels[i] = 0.0;
        m_targets[i] = 0.0;
    }
    connect(&m_timer, &QTimer::timeout, this, [this]() {
        if (m_playing) {
            // плавное «дыхание» столбиков
            for (int i = 0; i < m_targets.size(); ++i) {
                double base = 0.35 + 0.65 * qAbs(qSin(i * 0.55));
                double rnd = QRandomGenerator::global()->generateDouble(); // 0..1
                m_targets[i] = qBound(0.05, base * (0.35 + 0.65 * rnd), 1.0);
            }
        } else {
            for (int i = 0; i < m_targets.size(); ++i)
                m_targets[i] = 0.0;
        }
        for (int i = 0; i < m_levels.size(); ++i)
            m_levels[i] += (m_targets[i] - m_levels[i]) * (m_playing ? 0.35 : 0.12);
        update();
    });
    m_timer.start(33);
}

void Visualizer::setPlaying(bool on)
{
    m_playing = on;
}

void Visualizer::setColors(const QColor &accent, const QColor &accent2, const QColor &bg)
{
    m_accent = accent;
    m_accent2 = accent2;
    m_bg = bg;
    update();
}

void Visualizer::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    p.setPen(Qt::NoPen);

    const int n = m_levels.size();
    const double gap = 3.0;
    const double w = (width() - gap * (n - 1)) / double(n);
    const double h = double(height());
    const int baseY = height() - 4;

    for (int i = 0; i < n; ++i) {
        double lvl = m_levels[i];
        if (lvl <= 0.01)
            continue;
        double bh = qMax(3.0, lvl * (h - 10));
        QLinearGradient g(0, baseY - bh, 0, baseY);
        g.setColorAt(0.0, m_accent2);
        g.setColorAt(1.0, m_accent);
        p.setBrush(g);
        QRectF bar(4.0 + i * (w + gap), baseY - bh, w, bh);
        p.drawRoundedRect(bar, 2, 2);
    }
}
