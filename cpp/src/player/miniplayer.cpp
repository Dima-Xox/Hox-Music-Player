#include "miniplayer.h"
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QPainter>
#include <QPainterPath>

MiniPlayer::MiniPlayer(QWidget *parent)
    : QWidget(parent, Qt::Window | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint)
{
    setFixedSize(460, 116);

    m_cover = new QLabel(this);
    m_cover->setFixedSize(84, 84);
    m_cover->setAlignment(Qt::AlignCenter);

    m_title = new QLabel(this);
    m_title->setStyleSheet("font-size: 13px; font-weight: bold;");
    m_artist = new QLabel(this);
    m_artist->setStyleSheet("font-size: 11px;");

    m_prev = new QPushButton(QStringLiteral("⏮"), this);
    m_play = new QPushButton(QStringLiteral("▶"), this);
    m_next = new QPushButton(QStringLiteral("⏭"), this);
    for (QPushButton *b : { m_prev, m_play, m_next }) {
        b->setFixedSize(38, 30);
        b->setFlat(true);
        b->setCursor(Qt::PointingHandCursor);
    }

    m_pos = new QSlider(Qt::Horizontal, this);
    m_pos->setRange(0, 1000);
    m_pos->setFixedHeight(14);
    connect(m_pos, &QSlider::sliderReleased, this, [this]() {
        emit seekPreview(m_pos->value());
    });

    auto *right = new QVBoxLayout;
    right->setContentsMargins(0, 0, 0, 0);
    right->setSpacing(2);
    auto *rowBtn = new QHBoxLayout;
    rowBtn->setSpacing(6);
    rowBtn->addWidget(m_prev);
    rowBtn->addWidget(m_play);
    rowBtn->addWidget(m_next);
    rowBtn->addStretch(1);
    right->addWidget(m_title);
    right->addWidget(m_artist);
    right->addLayout(rowBtn);
    right->addWidget(m_pos);

    auto *top = new QHBoxLayout(this);
    top->setContentsMargins(12, 12, 12, 12);
    top->setSpacing(12);
    top->addWidget(m_cover);
    top->addLayout(right, 1);

    connect(m_play, &QPushButton::clicked, this, &MiniPlayer::playPauseRequested);
    connect(m_next, &QPushButton::clicked, this, &MiniPlayer::nextRequested);
    connect(m_prev, &QPushButton::clicked, this, &MiniPlayer::prevRequested);
}

void MiniPlayer::setTrack(const QString &title, const QString &artist, const QPixmap &cover)
{
    m_title->setText(title);
    m_title->setToolTip(title);
    m_artist->setText(artist);
    if (!cover.isNull()) {
        m_cover->setPixmap(cover.scaled(m_cover->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
    } else {
        QPixmap pm(m_cover->size());
        pm.fill(Qt::transparent);
        QPainter p(&pm);
        p.setRenderHint(QPainter::Antialiasing);
        p.setBrush(palette().color(QPalette::AlternateBase));
        p.setPen(Qt::NoPen);
        p.drawRoundedRect(pm.rect(), 8, 8);
        p.setPen(palette().color(QPalette::Mid));
        p.drawText(pm.rect(), Qt::AlignCenter, QStringLiteral("♪"));
        m_cover->setPixmap(pm);
    }
}

void MiniPlayer::setPosition(qint64 pos, qint64 dur)
{
    if (dur > 0 && !m_pos->isSliderDown())
        m_pos->setValue(int(1000.0 * double(pos) / double(dur)));
}

void MiniPlayer::setPlaying(bool on)
{
    m_play->setText(on ? QStringLiteral("⏸") : QStringLiteral("▶"));
}

void MiniPlayer::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    QPainterPath path;
    path.addRoundedRect(rect().adjusted(0, 0, -1, -1), 10, 10);
    p.fillPath(path, palette().color(QPalette::Window));
    p.setPen(palette().color(QPalette::Mid));
    p.drawPath(path);
}
