// Часть 2 класса MainWindow: воспроизведение, караоке, миниплеер, настройки,
// drag&drop, «Что нового», восстановление последнего трека.
#include "mainwindow.h"
#include "whatsnew.h"
#include "version.h"

#include <QFileDialog>
#include <QMessageBox>
#include <QTextBrowser>
#include <QDialog>
#include <QVBoxLayout>
#include <QSettings>
#include <QFileInfo>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QCloseEvent>
#include <QMimeData>
#include <QUrl>
#include <QTimer>
#include <QDesktopServices>
#include <QRandomGenerator>

// ---------------------------------------------------------------- воспроизведение
void MainWindow::playIndex(int row)
{
    if (row < 0 || row >= m_tracks.size())
        return;
    m_current = row;
    const Track &t = m_tracks[row];
    m_player->setSource(QUrl::fromLocalFile(t.path));
    m_player->play();
    m_seek->setRange(0, int(qMax<qint64>(t.durMs, 1)));
    m_seek->setValue(0);
    updateNowPlayingLabels();
    loadKaraoke(t);
    highlightPlayingRow();

    QSettings st;
    st.setValue(QStringLiteral("last_track"), t.path);
    st.setValue(QStringLiteral("last_pos"), 0);
}

void MainWindow::togglePlayPause()
{
    if (m_current < 0 || m_current >= m_tracks.size()) {
        if (!m_tracks.isEmpty())
            playIndex(0);
        return;
    }
    if (m_player->playbackState() == QMediaPlayer::PlayingState)
        m_player->pause();
    else
        m_player->play();
}

void MainWindow::nextTrack()
{
    if (m_tracks.isEmpty())
        return;
    int idx = m_current;
    if (m_repeat == 1 && m_current >= 0) {
        m_player->setPosition(0);
        m_player->play();
        return;
    }
    if (m_shuffle) {
        idx = (m_tracks.size() > 1)
                  ? (m_current + 1 + int(QRandomGenerator::global()->generate() % quint32(m_tracks.size() - 1)))
                        % m_tracks.size()
                  : 0;
        playIndex(idx);
        return;
    }
    idx = (m_current < 0 ? -1 : m_current) + 1;
    if (idx >= m_tracks.size()) {
        if (m_repeat == 2)
            idx = 0;
        else
            idx = m_tracks.size() - 1;
    }
    playIndex(idx);
}

void MainWindow::prevTrack()
{
    if (m_tracks.isEmpty())
        return;
    // v0.4.8-фикс: при ещё не выбранном треке (current_index = -1) формула
    // (-1 - 1) % N прыгала на ПРЕДпоследний трек; считаем от нуля
    const int start = (m_current >= 0) ? m_current : 0;
    playIndex((start - 1 + int(m_tracks.size())) % int(m_tracks.size()));
}

void MainWindow::positionChanged(qint64 pos)
{
    if (!m_seek->isSliderDown() && m_seek->maximum() > 0)
        m_seek->setValue(int(pos));
    const qint64 dur = qMax<qint64>(m_player->duration(), 0);
    m_lblTime->setText(fmtTime(pos) + QStringLiteral(" / ") + fmtTime(dur));
    if (m_karaoke && !m_karaoke->isEmpty())
        m_karaoke->setPosition(pos);
    if (m_mini && m_mini->isVisible())
        m_mini->setPosition(pos, dur);
}

void MainWindow::durationChangedMain(qint64 dur)
{
    if (dur > 0)
        m_seek->setRange(0, int(dur));
}

void MainWindow::seekSliderMoved()
{
    m_seekPressed = false;
    const int v = m_seek->value();
    m_player->setPosition(v);
    if (m_karaoke && !m_karaoke->isEmpty())
        m_karaoke->setPosition(v);
}

void MainWindow::volumeChanged(int v)
{
    m_audio->setVolume(v / 100.0);
    QSettings st;
    st.setValue(QStringLiteral("volume"), v);
}

void MainWindow::onPlayerError()
{
}

// ---------------------------------------------------------------- караоке
void MainWindow::loadKaraoke(const Track &t)
{
    QSettings st;
    m_karaoke->setOffset(st.value(QStringLiteral("lyric_offset_ms"), 0).toLongLong());

    QFileInfo fi(t.path);
    const QString lrcPath = fi.dir().filePath(fi.completeBaseName() + QStringLiteral(".lrc"));
    if (!t.hasLrc || !QFile::exists(lrcPath)) {
        m_karaoke->clear();
        return;
    }
    const QVector<LrcLine> lines = parseLrc(lrcPath);
    if (lines.isEmpty()) {
        m_karaoke->clear();
        return;
    }
    m_karaoke->setLyrics(lines, t.title);
    positionChanged(m_player->position());
}

void MainWindow::karaokeSeek(qint64 rawMs)
{
    m_player->setPosition(qMax<qint64>(0, rawMs));
    positionChanged(qMax<qint64>(0, rawMs));
}

void MainWindow::karaokeOffset(qint64 off)
{
    QSettings st;
    st.setValue(QStringLiteral("lyric_offset_ms"), off);
}

// ---------------------------------------------------------------- мини-плеер
void MainWindow::toggleMini()
{
    if (!m_mini) {
        m_mini = new MiniPlayer(nullptr);
        connect(m_mini, &MiniPlayer::playPauseRequested, this, &MainWindow::togglePlayPause);
        connect(m_mini, &MiniPlayer::nextRequested, this, &MainWindow::nextTrack);
        connect(m_mini, &MiniPlayer::prevRequested, this, &MainWindow::prevTrack);
        connect(m_mini, &MiniPlayer::seekPreview, this, [this](int pm) {
            const qint64 dur = qMax<qint64>(m_player->duration(), 1);
            const qint64 pos = qint64(double(pm) / 1000.0 * double(dur));
            m_player->setPosition(pos);
        });
        connect(m_mini, &MiniPlayer::closeRequested, m_mini, &MiniPlayer::hide);
    }
    if (m_mini->isVisible()) {
        m_mini->hide();
    } else {
        if (m_current >= 0 && m_current < m_tracks.size())
            m_mini->setTrack(m_tracks[m_current].title, m_tracks[m_current].artist,
                             coverPixmap(m_tracks[m_current], 84));
        m_mini->show();
        m_mini->raise();
    }
}

// ---------------------------------------------------------------- «Что нового»
void MainWindow::showWhatsNew()
{
    QDialog dlg(this);
    dlg.setWindowTitle(QStringLiteral("Что нового — список изменений по версиям"));
    dlg.resize(560, 520);
    auto *lay = new QVBoxLayout(&dlg);
    auto *tb = new QTextBrowser(&dlg);
    tb->setOpenExternalLinks(false);
    tb->setHtml(WhatsNew::html());
    lay->addWidget(tb);
    dlg.exec();
}

// ---------------------------------------------------------------- повторы/случайно
void MainWindow::cycleRepeat()
{
    m_repeat = (m_repeat + 1) % 3;
    m_btnRepeat->setText(m_repeat == 0 ? QStringLiteral("Повтор: выкл")
                        : m_repeat == 1 ? QStringLiteral("Повтор: один")
                                        : QStringLiteral("Повтор: все"));
    QSettings st;
    st.setValue(QStringLiteral("repeat"), m_repeat);
}

void MainWindow::toggleShuffle()
{
    m_shuffle = !m_shuffle;
    m_btnShuffle->setText(m_shuffle ? QStringLiteral("Случайно: вкл")
                                    : QStringLiteral("Случайно: выкл"));
    QSettings st;
    st.setValue(QStringLiteral("shuffle"), m_shuffle);
}

// ---------------------------------------------------------------- drag&drop
void MainWindow::dragEnterEvent(QDragEnterEvent *e)
{
    if (e->mimeData()->hasUrls()) {
        e->acceptProposedAction();
    }
}

void MainWindow::dropEvent(QDropEvent *e)
{
    QStringList files;
    bool hasFolder = false;
    for (const QUrl &u : e->mimeData()->urls()) {
        QFileInfo fi(u.toLocalFile());
        if (fi.isDir())
            hasFolder = true;
        else if (fi.isFile())
            files.append(fi.absoluteFilePath());
    }
    if (hasFolder) {
        addFolder();
        return;
    }
    if (files.isEmpty())
        return;
    // добавляем файлы в библиотеку и играем первый
    const int first = m_tracks.size();
    for (const QString &f : files) {
        QFileInfo fi(f);
        Track t;
        t.path = f;
        int ln = leadingNumber(fi.fileName());
        t.num = (ln >= 0) ? ln : -1;
        t.title = fi.completeBaseName();
        t.hasLrc = QFile::exists(fi.dir().filePath(fi.completeBaseName() + QStringLiteral(".lrc")));
        m_tracks.append(t);
    }
    rebuildTable();
    refreshStatus();
    playIndex(first);
}

void MainWindow::closeEvent(QCloseEvent *e)
{
    saveSettings();
    if (m_mini && m_mini->isVisible())
        m_mini->close();
    e->accept();
}

void MainWindow::startPlaybackFromArgs(const QStringList &paths)
{
    if (paths.isEmpty())
        return;
    const int first = m_tracks.size();
    for (const QString &p : paths) {
        QFileInfo fi(p);
        if (!fi.isFile())
            continue;
        Track t;
        t.path = fi.absoluteFilePath();
        int ln = leadingNumber(fi.fileName());
        t.num = (ln >= 0) ? ln : -1;
        t.title = fi.completeBaseName();
        t.hasLrc = QFile::exists(fi.dir().filePath(fi.completeBaseName() + QStringLiteral(".lrc")));
        m_tracks.append(t);
    }
    if (m_tracks.size() > first) {
        rebuildTable();
        playIndex(first);
    }
}

// ---------------------------------------------------------------- настройки
void MainWindow::saveSettings()
{
    QSettings st;
    st.setValue(QStringLiteral("geometry"), saveGeometry());
    st.setValue(QStringLiteral("volume"), m_volume->value());
    st.setValue(QStringLiteral("theme"), m_themeBox->currentIndex());
    st.setValue(QStringLiteral("repeat"), m_repeat);
    st.setValue(QStringLiteral("shuffle"), m_shuffle);
    st.setValue(QStringLiteral("lyric_offset_ms"), m_karaoke ? m_karaoke->offset() : 0);
    st.setValue(QStringLiteral("last_track"), (m_current >= 0 && m_current < m_tracks.size())
                                                 ? m_tracks[m_current].path
                                                 : st.value(QStringLiteral("last_track")).toString());
    st.setValue(QStringLiteral("last_pos"), m_player->position());
    st.setValue(QStringLiteral("mini_visible"), (m_mini && m_mini->isVisible()));
}

void MainWindow::loadSettings()
{
    QSettings st;
    const QByteArray geo = st.value(QStringLiteral("geometry")).toByteArray();
    if (!geo.isEmpty())
        restoreGeometry(geo);
    m_volume->setValue(st.value(QStringLiteral("volume"), 80).toInt());
    m_audio->setVolume(m_volume->value() / 100.0);
    m_themeBox->setCurrentIndex(st.value(QStringLiteral("theme"), 0).toInt());
    m_repeat = st.value(QStringLiteral("repeat"), 0).toInt();
    m_shuffle = st.value(QStringLiteral("shuffle"), false).toBool();
    m_btnRepeat->setText(m_repeat == 0 ? QStringLiteral("Повтор: выкл")
                        : m_repeat == 1 ? QStringLiteral("Повтор: один")
                                        : QStringLiteral("Повтор: все"));
    m_btnShuffle->setText(m_shuffle ? QStringLiteral("Случайно: вкл")
                                    : QStringLiteral("Случайно: выкл"));
    if (m_karaoke)
        m_karaoke->setOffset(st.value(QStringLiteral("lyric_offset_ms"), 0).toLongLong());
}

void MainWindow::restoreLast()
{
    // библиотека восстанавливается из сохранённых папок
    QSettings st;
    const QStringList folders = st.value(QStringLiteral("folders")).toStringList();
    if (folders.isEmpty())
        return;
    rescanAll();
    // после сканирования (асинхронного) трек восстановится по пути
    QTimer::singleShot(1500, this, [this]() {
        QSettings st;
        const QString last = st.value(QStringLiteral("last_track")).toString();
        if (last.isEmpty())
            return;
        const int r = rowByPath(last);
        if (r < 0 || r >= m_tracks.size())
            return;
        m_current = r;
        m_player->setSource(QUrl::fromLocalFile(last));
        m_seek->setRange(0, int(qMax<qint64>(m_tracks[r].durMs, 1)));
        const qint64 pos = st.value(QStringLiteral("last_pos"), 0).toLongLong();
        if (pos > 0 && pos < m_tracks[r].durMs - 1000)
            m_player->setPosition(pos);
        updateNowPlayingLabels();
        loadKaraoke(m_tracks[r]);
        highlightPlayingRow();
    });
}
