#include "mainwindow.h"
#include "whatsnew.h"
#include "version.h"
#include "karaoke.h"
#include "visualizer.h"
#include "miniplayer.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QFileDialog>
#include <QMenu>
#include <QMessageBox>
#include <QTextBrowser>
#include <QDialog>
#include <QSettings>
#include <QStandardPaths>
#include <QDir>
#include <QDirIterator>
#include <QFileInfo>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QCloseEvent>
#include <QUrl>
#include <QFile>
#include <QStatusBar>
#include <QDesktopServices>
#include <QPainter>
#include <QScreen>
#include <QGuiApplication>
#include <QRegularExpression>
#include <QMediaMetaData>
#include <QShortcut>
#include <algorithm>

// ---------------------------------------------------------------- темы
static QVector<Theme> buildThemes()
{
    return {
        { QStringLiteral("Aurora"),
          QColor(0x0b, 0x10, 0x20), QColor(0x11, 0x1a, 0x33), QColor(0x14, 0x1f, 0x3a),
          QColor(0xe8, 0xec, 0xf5), QColor(0x8f, 0x9c, 0xb8), QColor(0x4f, 0xc3, 0xf7), QColor(0x7c, 0x4d, 0xff) },
        { QStringLiteral("Ember"),
          QColor(0x16, 0x0d, 0x0a), QColor(0x24, 0x12, 0x0c), QColor(0x2a, 0x16, 0x0f),
          QColor(0xf5, 0xe8, 0xdf), QColor(0xb0, 0x8f, 0x7a), QColor(0xff, 0x7a, 0x3c), QColor(0xd9, 0x3a, 0x3a) },
        { QStringLiteral("Classic"),
          QColor(0xf5, 0xf5, 0xf4), QColor(0xe8, 0xe8, 0xe6), QColor(0xdf, 0xdf, 0xdc),
          QColor(0x1c, 0x1c, 0x1c), QColor(0x6b, 0x6b, 0x6b), QColor(0x0a, 0x66, 0xc2), QColor(0x00, 0x51, 0x9e) },
        { QStringLiteral("Sunset"),
          QColor(0x1c, 0x10, 0x24), QColor(0x2a, 0x16, 0x33), QColor(0x30, 0x1b, 0x3a),
          QColor(0xf8, 0xec, 0xf5), QColor(0xb3, 0x95, 0xb8), QColor(0xff, 0x5e, 0x8a), QColor(0xff, 0x9d, 0x4d) },
        { QStringLiteral("Emerald"),
          QColor(0x08, 0x16, 0x11), QColor(0x0d, 0x24, 0x1a), QColor(0x10, 0x2a, 0x1f),
          QColor(0xe4, 0xf5, 0xec), QColor(0x8a, 0xb5, 0x9d), QColor(0x2e, 0xc4, 0x8b), QColor(0x1e, 0x9e, 0x77) },
    };
}

MainWindow::MainWindow(bool selfcheck, QWidget *parent)
    : QMainWindow(parent), m_selfcheck(selfcheck)
{
    setWindowTitle(QStringLiteral(APP_NAME) + QStringLiteral(" v") + QStringLiteral(APP_VERSION));
    setWindowIcon(QIcon(QStringLiteral(":/app.ico")));
    setAcceptDrops(true);
    resize(1120, 720);
    setMinimumSize(940, 560);

    m_player = new QMediaPlayer(this);
    m_audio = new QAudioOutput(this);
    m_player->setAudioOutput(m_audio);

    m_probe = new QMediaPlayer(this);
    m_probeTimeout.setSingleShot(true);
    connect(&m_probeTimeout, &QTimer::timeout, this, &MainWindow::probeNext);

    connect(m_player, &QMediaPlayer::positionChanged, this, &MainWindow::positionChanged);
    connect(m_player, &QMediaPlayer::durationChanged, this, &MainWindow::durationChangedMain);
    connect(m_player, &QMediaPlayer::errorOccurred, this, [this](QMediaPlayer::Error, const QString &s) {
        if (m_current >= 0 && m_current < m_tracks.size())
            m_status->showMessage(QStringLiteral("Ошибка воспроизведения: %1 (%2)")
                                      .arg(s, QFileInfo(m_tracks[m_current].path).fileName()), 6000);
    });
    connect(m_player, &QMediaPlayer::playbackStateChanged, this, [this](QMediaPlayer::PlaybackState st) {
        m_btnPlay->setText(st == QMediaPlayer::PlayingState ? QStringLiteral("⏸") : QStringLiteral("▶"));
        m_viz->setPlaying(st == QMediaPlayer::PlayingState);
        if (m_mini)
            m_mini->setPlaying(st == QMediaPlayer::PlayingState);
    });

    buildUi();

    m_themeBox->setCurrentIndex(-1);
    loadSettings();
    if (m_themeBox->currentIndex() < 0)
        m_themeBox->setCurrentIndex(0);
    applyTheme(m_themeBox->currentIndex());

    restoreLast();

    if (m_selfcheck) {
        // режим самопроверки (CI): создаём окно, ничего не показываем
        QTimer::singleShot(300, this, [this]() {
            qInfo("HoxPlayer selfcheck OK %s", APP_VERSION);
            QApplication::exit(0);
        });
        return;
    }

    show();

    // «Что нового» — один раз на версию
    QSettings st;
    if (st.value(QStringLiteral("whatsnew_seen")).toString() != QStringLiteral(APP_VERSION)) {
        showWhatsNew();
        st.setValue(QStringLiteral("whatsnew_seen"), QStringLiteral(APP_VERSION));
    }
}

MainWindow::~MainWindow() = default;

// ---------------------------------------------------------------- UI
void MainWindow::buildUi()
{
    QWidget *central = new QWidget(this);
    setCentralWidget(central);
    QHBoxLayout *root = new QHBoxLayout(central);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    // ---- левая панель
    QWidget *left = new QWidget(this);
    left->setFixedWidth(320);
    QVBoxLayout *lv = new QVBoxLayout(left);
    lv->setContentsMargins(18, 18, 18, 14);
    lv->setSpacing(10);

    m_logo = new QLabel(this);
    m_logo->setAlignment(Qt::AlignCenter);
    {
        QPixmap pm(280, 64);
        pm.fill(Qt::transparent);
        QPainter p(&pm);
        p.setRenderHint(QPainter::Antialiasing);
        QFont f = font();
        f.setPointSize(30);
        f.setBold(true);
        p.setFont(f);
        p.setPen(Qt::white);
        p.drawText(pm.rect().adjusted(0, -6, 0, -6), Qt::AlignCenter, QStringLiteral("Hox"));
        QFont f2 = font();
        f2.setPointSize(9);
        p.setFont(f2);
        p.setPen(QColor(160, 175, 205));
        p.drawText(pm.rect().adjusted(0, 40, 0, 0), Qt::AlignCenter,
                   QStringLiteral("MUSIC PLAYER  v") + QStringLiteral(APP_VERSION));
        m_logo->setPixmap(pm);
    }

    m_cover = new QLabel(this);
    m_cover->setFixedSize(220, 220);
    m_cover->setAlignment(Qt::AlignCenter);
    m_cover->setStyleSheet(QStringLiteral(
        "border-radius: 10px; background: rgba(255,255,255,0.05);"));

    m_lblTitle = new QLabel(QStringLiteral("<нет трека>"), this);
    m_lblTitle->setAlignment(Qt::AlignCenter);
    m_lblTitle->setWordWrap(true);
    m_lblTitle->setStyleSheet(QStringLiteral("font-size: 15px; font-weight: bold;"));
    m_lblArtist = new QLabel(QString(), this);
    m_lblArtist->setAlignment(Qt::AlignCenter);
    m_lblArtist->setStyleSheet(QStringLiteral("font-size: 12px;"));

    m_seek = new QSlider(Qt::Horizontal, this);
    m_seek->setRange(0, 0);
    connect(m_seek, &QSlider::sliderPressed, this, [this]() { m_seekPressed = true; });
    connect(m_seek, &QSlider::sliderReleased, this, &MainWindow::seekSliderMoved);

    m_lblTime = new QLabel(QStringLiteral("0:00 / 0:00"), this);
    m_lblTime->setAlignment(Qt::AlignCenter);
    m_lblTime->setStyleSheet(QStringLiteral("font-size: 11px;"));

    QHBoxLayout *transport = new QHBoxLayout;
    transport->setSpacing(8);
    m_btnPrev = new QPushButton(QStringLiteral("⏮"), this);
    m_btnPlay = new QPushButton(QStringLiteral("▶"), this);
    m_btnNext = new QPushButton(QStringLiteral("⏭"), this);
    m_btnPrev->setFixedSize(52, 40);
    m_btnPlay->setFixedSize(64, 44);
    m_btnNext->setFixedSize(52, 40);
    m_btnPlay->setObjectName(QStringLiteral("btnPlay"));
    for (QPushButton *b : { m_btnPrev, m_btnPlay, m_btnNext })
        b->setCursor(Qt::PointingHandCursor);
    transport->addStretch(1);
    transport->addWidget(m_btnPrev);
    transport->addWidget(m_btnPlay);
    transport->addWidget(m_btnNext);
    transport->addStretch(1);

    QHBoxLayout *volRow = new QHBoxLayout;
    QLabel *volIco = new QLabel(QStringLiteral("🔊"), this);
    m_volume = new QSlider(Qt::Horizontal, this);
    m_volume->setRange(0, 100);
    m_volume->setValue(80);
    volRow->addWidget(volIco);
    volRow->addWidget(m_volume, 1);

    m_viz = new Visualizer(this);
    m_viz->setFixedHeight(64);

    lv->addWidget(m_logo);
    lv->addWidget(m_cover, 0, Qt::AlignHCenter);
    lv->addWidget(m_lblTitle);
    lv->addWidget(m_lblArtist);
    lv->addWidget(m_seek);
    lv->addWidget(m_lblTime);
    lv->addLayout(transport);
    lv->addLayout(volRow);
    lv->addStretch(1);
    lv->addWidget(m_viz);

    // ---- правая часть
    QWidget *right = new QWidget(this);
    QVBoxLayout *rv = new QVBoxLayout(right);
    rv->setContentsMargins(12, 14, 14, 10);
    rv->setSpacing(8);

    QHBoxLayout *tb = new QHBoxLayout;
    tb->setSpacing(6);
    auto mkBtn = [this](const QString &text, const char *tip) {
        QToolButton *b = new QToolButton(this);
        b->setText(text);
        b->setToolTip(QString::fromUtf8(tip));
        b->setCursor(Qt::PointingHandCursor);
        return b;
    };
    QToolButton *bAdd = mkBtn(QStringLiteral("＋ Папка"), "Добавить папку в библиотеку");
    QToolButton *bDel = mkBtn(QStringLiteral("－"), "Удалить папку из библиотеки");
    QToolButton *bScan = mkBtn(QStringLiteral("⟳"), "Пересканировать библиотеку");
    QToolButton *bMini = mkBtn(QStringLiteral("◱ Мини-плеер"), "Показать/скрыть мини-плеер");
    QToolButton *bWhats = mkBtn(QStringLiteral("Что нового"), "Список изменений по версиям");
    QToolButton *bPlayAll = mkBtn(QStringLiteral("▶ Всё"), "Играть все треки библиотеки");
    m_btnRepeat = mkBtn(QStringLiteral("Повтор: выкл"), "Повтор: выкл → один → все");
    m_btnShuffle = mkBtn(QStringLiteral("Случайно: выкл"), "Случайный порядок");
    m_themeBox = new QComboBox(this);
    for (const Theme &t : buildThemes())
        m_themeBox->addItem(t.name);
    m_themeBox->setToolTip(QStringLiteral("Тема оформления"));
    tb->addWidget(bAdd);
    tb->addWidget(bDel);
    tb->addWidget(bScan);
    tb->addSpacing(8);
    tb->addWidget(bPlayAll);
    tb->addWidget(m_btnRepeat);
    tb->addWidget(m_btnShuffle);
    tb->addSpacing(8);
    tb->addWidget(bMini);
    tb->addWidget(bWhats);
    tb->addStretch(1);
    tb->addWidget(new QLabel(QStringLiteral("Тема:"), this));
    tb->addWidget(m_themeBox);

    m_table = new QTableWidget(this);
    m_table->setColumnCount(5);
    m_table->setHorizontalHeaderLabels({ QStringLiteral("№"), QStringLiteral("Название"),
                                         QStringLiteral("Исполнитель"), QStringLiteral("Альбом"),
                                         QStringLiteral("Длительность") });
    m_table->verticalHeader()->setVisible(false);
    m_table->verticalHeader()->setDefaultSectionSize(30);
    m_table->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_table->setSelectionMode(QAbstractItemView::SingleSelection);
    m_table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_table->setShowGrid(false);
    m_table->setContextMenuPolicy(Qt::CustomContextMenu);
    m_table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Fixed);
    m_table->setColumnWidth(0, 56);
    m_table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Interactive);
    m_table->setColumnWidth(2, 190);
    m_table->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Interactive);
    m_table->setColumnWidth(3, 190);
    m_table->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Fixed);
    m_table->setColumnWidth(4, 90);
    m_table->setSortingEnabled(false);
    connect(m_table, &QTableWidget::cellDoubleClicked, this, &MainWindow::onCellDouble);
    connect(m_table, &QTableWidget::cellClicked, this, &MainWindow::onCellClicked);
    connect(m_table->horizontalHeader(), &QHeaderView::sortIndicatorChanged,
            this, &MainWindow::onSortChanged);
    connect(m_table, &QTableWidget::customContextMenuRequested, this, [this](const QPoint &pos) {
        int row = m_table->rowAt(pos.y());
        if (row < 0 || row >= m_tracks.size())
            return;
        QMenu menu(this);
        QAction *aPlay = menu.addAction(QStringLiteral("Играть"));
        QAction *aAlbum = menu.addAction(QStringLiteral("Воспроизвести альбом"));
        QAction *aArtist = menu.addAction(QStringLiteral("Играть все треки исполнителя"));
        menu.addSeparator();
        QAction *aShow = menu.addAction(QStringLiteral("Показать в папке"));
        QAction *aDrop = menu.addAction(QStringLiteral("Убрать из библиотеки"));
        QAction *chosen = menu.exec(m_table->viewport()->mapToGlobal(pos));
        if (chosen == aPlay) playIndex(row);
        else if (chosen == aAlbum) {
            // v0.4.8-фикс: альбом играется В ПОРЯДКЕ ЕГО ТРЕКОВ, затем остальная библиотека
            const QString alb = m_tracks[row].album;
            QVector<int> order;
            for (int i = 0; i < m_tracks.size(); ++i)
                if (m_tracks[i].album == alb) order.append(i);
            for (int i = 0; i < m_tracks.size(); ++i)
                if (m_tracks[i].album != alb) order.append(i);
            repopulateTable(order);
            playIndex(0);
        } else if (chosen == aArtist) {
            const QString art = m_tracks[row].artist;
            QVector<int> order;
            for (int i = 0; i < m_tracks.size(); ++i)
                if (m_tracks[i].artist == art) order.append(i);
            for (int i = 0; i < m_tracks.size(); ++i)
                if (m_tracks[i].artist != art) order.append(i);
            repopulateTable(order);
            playIndex(0);
        } else if (chosen == aShow) {
            QDesktopServices::openUrl(QUrl::fromLocalFile(QFileInfo(m_tracks[row].path).absolutePath()));
        } else if (chosen == aDrop) {
            m_tracks.remove(row);
            rebuildTable();
            refreshStatus();
        }
    });

    m_karaoke = new KaraokeWidget(this);
    m_karaoke->setVisible(false);
    m_karaoke->setFixedHeight(150);
    connect(m_karaoke, &KaraokeWidget::seekRequested, this, &MainWindow::karaokeSeek);
    connect(m_karaoke, &KaraokeWidget::offsetChanged, this, &MainWindow::karaokeOffset);

    rv->addLayout(tb);
    rv->addWidget(m_table, 1);
    rv->addWidget(m_karaoke);

    root->addWidget(left);
    root->addWidget(right, 1);

    m_status = statusBar();
    refreshStatus();

    // ---- связи
    connect(m_btnPrev, &QPushButton::clicked, this, &MainWindow::prevTrack);
    connect(m_btnNext, &QPushButton::clicked, this, &MainWindow::nextTrack);
    connect(m_btnPlay, &QPushButton::clicked, this, &MainWindow::togglePlayPause);
    connect(m_volume, &QSlider::valueChanged, this, &MainWindow::volumeChanged);
    connect(m_themeBox, &QComboBox::currentIndexChanged, this, &MainWindow::themeSelected);
    connect(bAdd, &QToolButton::clicked, this, &MainWindow::addFolder);
    connect(bDel, &QToolButton::clicked, this, &MainWindow::removeFolderMenu);
    connect(bScan, &QToolButton::clicked, this, &MainWindow::rescanAll);
    connect(bMini, &QToolButton::clicked, this, &MainWindow::toggleMini);
    connect(bWhats, &QToolButton::clicked, this, &MainWindow::showWhatsNew);
    connect(bPlayAll, &QToolButton::clicked, this, [this]() {
        if (!m_tracks.isEmpty()) {
            rebuildTable();
            playIndex(0);
        }
    });
    connect(m_btnRepeat, &QToolButton::clicked, this, &MainWindow::cycleRepeat);
    connect(m_btnShuffle, &QToolButton::clicked, this, &MainWindow::toggleShuffle);

    // сканер
    connect(m_probe, &QMediaPlayer::mediaStatusChanged, this, &MainWindow::probeStatus);

    QShortcut *sp = new QShortcut(QKeySequence(Qt::Key_Space), this);
    connect(sp, &QShortcut::activated, this, &MainWindow::togglePlayPause);
    QShortcut *sn = new QShortcut(QKeySequence(Qt::Key_MediaNext), this);
    connect(sn, &QShortcut::activated, this, &MainWindow::nextTrack);
    QShortcut *spb = new QShortcut(QKeySequence(Qt::Key_MediaPrevious), this);
    connect(spb, &QShortcut::activated, this, &MainWindow::prevTrack);
    QShortcut *spp = new QShortcut(QKeySequence(Qt::Key_MediaPlay), this);
    connect(spp, &QShortcut::activated, this, &MainWindow::togglePlayPause);
}

void MainWindow::applyTheme(int idx)
{
    const QVector<Theme> themes = buildThemes();
    if (idx < 0 || idx >= themes.size())
        return;
    const Theme &t = themes[idx];
    QSettings st;
    st.setValue(QStringLiteral("theme"), idx);

    const QString qss = QStringLiteral(
        "QWidget { background: %1; color: %2; font-size: 12px; }"
        "QLabel { background: transparent; }"
        "QTableWidget { background: %1; alternate-background-color: %3; color: %2; border: none; }"
        "QHeaderView::section { background: %3; color: %4; border: none; padding: 6px; font-weight: bold; }"
        "QTableWidget::item { padding: 4px; border: none; }"
        "QTableWidget::item:selected { background: %5; color: %1; }"
        "QToolButton, QPushButton { background: %3; color: %2; border: 1px solid %6;"
        "  border-radius: 8px; padding: 6px 10px; }"
        "QPushButton#btnPlay { background: %5; color: %1; font-size: 16px; border: none; }"
        "QToolButton:hover, QPushButton:hover { border-color: %5; }"
        "QComboBox { background: %3; color: %2; border: 1px solid %6; border-radius: 8px; padding: 4px 8px; }"
        "QComboBox QAbstractItemView { background: %3; color: %2; selection-background-color: %5; }"
        "QSlider::groove:horizontal { height: 5px; background: %6; border-radius: 3px; }"
        "QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: %5; }"
        "QSlider::sub-page:horizontal { background: %5; border-radius: 3px; }"
        "QStatusBar { background: %3; color: %4; }"
        "QMenu { background: %3; color: %2; border: 1px solid %6; }"
        "QMenu::item:selected { background: %5; color: %1; }"
        "QScrollBar:vertical { background: transparent; width: 10px; }"
        "QScrollBar::handle:vertical { background: %6; border-radius: 5px; min-height: 30px; }"
        "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        .arg(t.bg.name(), t.text.name(), t.panel.name(), t.sub.name(),
             t.accent.name(), QColor(t.panel.lightness() < 128 ? t.text.red() : 0,
                                     t.panel.lightness() < 128 ? t.text.green() : 0,
                                     t.panel.lightness() < 128 ? t.text.blue() : 0, 40).name());
    // линия-разделитель — упрощённо через цвет панели
    qApp->setStyleSheet(qss);

    m_viz->setColors(t.accent, t.accent2, t.bg);
    update();
}

void MainWindow::themeSelected(int idx)
{
    if (idx >= 0)
        applyTheme(idx);
}

void MainWindow::refreshStatus()
{
    QSettings st;
    const QStringList folders = st.value(QStringLiteral("folders")).toStringList();
    m_status->showMessage(QStringLiteral("Треков: %1   |   Папок: %2").arg(m_tracks.size()).arg(folders.size()));
}

QString MainWindow::fmtTime(qint64 ms) const
{
    if (ms < 0)
        ms = 0;
    const qint64 s = ms / 1000;
    return QStringLiteral("%1:%2").arg(s / 60).arg(s % 60, 2, 10, QLatin1Char('0'));
}

int MainWindow::leadingNumber(const QString &fileName) const
{
    static const QRegularExpression re(QStringLiteral(R"(^\s*(\d{1,4})[\s.\-_–—)]+)"));
    auto m = re.match(fileName);
    if (m.hasMatch())
        return m.captured(1).toInt();
    return -1;
}

QPixmap MainWindow::coverPixmap(const Track &t, int side) const
{
    QPixmap pm;
    if (!t.cover.isEmpty())
        pm.loadFromData(t.cover);
    if (pm.isNull()) {
        pm = QPixmap(side, side);
        pm.fill(Qt::transparent);
        QPainter p(&pm);
        p.setRenderHint(QPainter::Antialiasing);
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(255, 255, 255, 16));
        p.drawRoundedRect(pm.rect(), 10, 10);
        p.setPen(QColor(255, 255, 255, 90));
        p.drawText(pm.rect(), Qt::AlignCenter, QStringLiteral("♪"));
        return pm;
    }
    return pm.scaled(side, side, Qt::KeepAspectRatioByExpanding, Qt::SmoothTransformation);
}

// ---------------------------------------------------------------- сканирование
void MainWindow::addFolder()
{
    QString dir = QFileDialog::getExistingDirectory(this, QStringLiteral("Добавить папку с музыкой"));
    if (dir.isEmpty())
        return;
    QSettings st;
    QStringList folders = st.value(QStringLiteral("folders")).toStringList();
    if (!folders.contains(dir)) {
        folders.append(dir);
        st.setValue(QStringLiteral("folders"), folders);
    }
    rescanAll();
}

void MainWindow::removeFolderMenu()
{
    QSettings st;
    QStringList folders = st.value(QStringLiteral("folders")).toStringList();
    if (folders.isEmpty())
        return;
    QMenu menu(this);
    QList<QAction *> acts;
    for (const QString &f : folders)
        acts.append(menu.addAction(f));
    QAction *chosen = menu.exec(mapToGlobal(QPoint(m_table->x() + 40, m_table->y() + 10)));
    if (!chosen)
        return;
    folders.removeAll(chosen->text());
    st.setValue(QStringLiteral("folders"), folders);
    rescanAll();
}

void MainWindow::rescanAll()
{
    QSettings st;
    const QStringList folders = st.value(QStringLiteral("folders")).toStringList();

    m_tracks.clear();
    rebuildTable();
    m_current = -1;
    m_scanQueue.clear();

    static const QStringList suffixes = { QStringLiteral("mp3"), QStringLiteral("wav"),
                                          QStringLiteral("ogg"), QStringLiteral("flac"),
                                          QStringLiteral("m4a"), QStringLiteral("aac"),
                                          QStringLiteral("opus"), QStringLiteral("wma") };

    for (const QString &f : folders) {
        QDirIterator it(f, QDir::Files | QDir::Readable, QDirIterator::Subdirectories);
        int idx = 0;
        while (it.hasNext()) {
            const QString path = it.next();
            QFileInfo fi(path);
            if (!suffixes.contains(fi.suffix().toLower()))
                continue;
            Track t;
            t.path = path;
            int ln = leadingNumber(fi.fileName());
            t.num = (ln >= 0) ? ln : idx;   // нет номера — порядковый в папке
            t.hasLrc = QFile::exists(fi.dir().filePath(fi.completeBaseName() + QStringLiteral(".lrc")));
            m_tracks.append(t);
            m_scanQueue.append(path);
            ++idx;
        }
    }
    m_scanDone = 0;
    m_status->showMessage(QStringLiteral("Сканирование: файлов %1").arg(m_scanQueue.size()));
    m_probe->stop();
    m_probeTimeout.stop();
    m_scanActive = false;
    probeNext();
    refreshStatus();
}

void MainWindow::probeNext()
{
    m_probeTimeout.stop();
    if (m_scanQueue.isEmpty()) {
        m_scanActive = false;
        m_probe->stop();
        m_probe->setSource(QUrl());
        m_status->showMessage(QStringLiteral("Библиотека: %1 треков").arg(m_tracks.size()), 4000);
        rebuildTable();
        refreshStatus();
        highlightPlayingRow();
        return;
    }
    m_probePath = m_scanQueue.takeFirst();
    m_scanActive = true;
    m_probe->setSource(QUrl::fromLocalFile(m_probePath));
    m_probeTimeout.start(5000);
}

void MainWindow::probeStatus(QMediaPlayer::MediaStatus st)
{
    if (!m_scanActive)
        return;
    if (st == QMediaPlayer::LoadedMedia || st == QMediaPlayer::BufferedMedia
        || st == QMediaPlayer::EndOfMedia || st == QMediaPlayer::InvalidMedia) {
        int idx = -1;
        for (int i = 0; i < m_tracks.size(); ++i) {
            if (m_tracks[i].path == m_probePath) { idx = i; break; }
        }
        if (idx < 0) {
            probeNext();
            return;
        }
        Track &t = m_tracks[idx];
        const QMediaMetaData md = m_probe->metaData();
        const QString mTitle = md.stringValue(QMediaMetaData::Title);
        const QString mArtist = md.stringValue(QMediaMetaData::Artist);
        const QString mAlbum = md.stringValue(QMediaMetaData::Album);
        if (!mTitle.isEmpty())
            t.title = mTitle;
        if (!mArtist.isEmpty())
            t.artist = mArtist;
        if (!mAlbum.isEmpty())
            t.album = mAlbum;
        t.durMs = m_probe->duration();
        const QVariant cov = md.value(QMediaMetaData::CoverArtImage);
        if (cov.isValid())
            t.cover = cov.toByteArray();

        // если теги пусты — разбор имени «Исполнитель - Название»
        if (t.title.isEmpty()) {
            QFileInfo fi(t.path);
            QString base = fi.completeBaseName();
            static const QRegularExpression numRe(QStringLiteral(R"(^\s*\d{1,4}[\s.\-_–—)]+)"));
            base.remove(numRe);
            const int dash = base.indexOf(QStringLiteral(" - "));
            if (dash > 0) {
                if (t.artist.isEmpty())
                    t.artist = base.left(dash).trimmed();
                t.title = base.mid(dash + 3).trimmed();
            } else {
                t.title = base.trimmed();
            }
        }
        if (++m_scanDone % 5 == 0)
            m_status->showMessage(QStringLiteral("Сканирование: %1/%2").arg(m_scanDone).arg(m_scanDone + m_scanQueue.size()));
        probeNext();
    }
}

// ---------------------------------------------------------------- таблица
void MainWindow::rebuildTable()
{
    QVector<int> order;
    order.reserve(m_tracks.size());
    for (int i = 0; i < m_tracks.size(); ++i)
        order.append(i);
    repopulateTable(order);
}

void MainWindow::repopulateTable(const QVector<int> &order)
{
    m_table->setSortingEnabled(false);
    m_table->setRowCount(0);
    QVector<Track> nt;
    nt.reserve(order.size());
    m_table->setRowCount(order.size());
    for (int r = 0; r < order.size(); ++r) {
        if (order[r] < 0 || order[r] >= m_tracks.size())
            continue;
        const Track &t = m_tracks[order[r]];
        nt.append(t);

        auto numItem = new QTableWidgetItem();
        // v0.4.8-фикс: номер «0» — валидный номер, отображаем его
        if (t.num >= 0)
            numItem->setData(Qt::DisplayRole, t.num);
        else
            numItem->setData(Qt::DisplayRole, QStringLiteral("–"));
        numItem->setTextAlignment(Qt::AlignCenter);
        numItem->setData(Qt::UserRole, t.path);
        m_table->setItem(r, 0, numItem);

        auto title = new QTableWidgetItem(t.title.isEmpty() ? QFileInfo(t.path).fileName() : t.title);
        title->setData(Qt::UserRole, t.path);
        m_table->setItem(r, 1, title);

        m_table->setItem(r, 2, new QTableWidgetItem(t.artist));
        m_table->setItem(r, 3, new QTableWidgetItem(t.album));

        auto dur = new QTableWidgetItem(fmtTime(t.durMs));
        dur->setTextAlignment(Qt::AlignRight | Qt::AlignVCenter);
        dur->setData(Qt::UserRole, t.path);
        m_table->setItem(r, 4, dur);
    }
    m_tracks = nt;
    m_table->setSortingEnabled(true);
    if (m_current >= m_tracks.size())
        m_current = -1;
    highlightPlayingRow();
}

int MainWindow::rowByPath(const QString &path) const
{
    for (int r = 0; r < m_table->rowCount(); ++r) {
        QTableWidgetItem *it = m_table->item(r, 1);
        if (it && it->data(Qt::UserRole).toString() == path)
            return r;
    }
    return -1;
}

void MainWindow::onSortChanged(int col, Qt::SortOrder order)
{
    Q_UNUSED(col);
    // таблица и есть плейлист: пересобираем m_tracks в порядке отображения
    m_table->setSortingEnabled(false);
    QVector<Track> nt;
    nt.reserve(m_table->rowCount());
    for (int r = 0; r < m_table->rowCount(); ++r) {
        QTableWidgetItem *it = m_table->item(r, 1);
        const QString path = it ? it->data(Qt::UserRole).toString() : QString();
        int idx = -1;
        for (int i = 0; i < m_tracks.size(); ++i) {
            if (m_tracks[i].path == path) { idx = i; break; }
        }
        if (idx >= 0)
            nt.append(m_tracks[idx]);
    }
    m_tracks = nt;
    // перепривязываем текущий трек
    if (m_current >= 0 && m_current < m_tracks.size()) {
        const QString cur = m_player->source().toLocalFile();
        if (!cur.isEmpty()) {
            for (int i = 0; i < m_tracks.size(); ++i) {
                if (m_tracks[i].path == cur) { m_current = i; break; }
            }
        }
    }
    m_table->setSortingEnabled(true);
}

void MainWindow::onCellClicked(int row, int col)
{
    Q_UNUSED(col);
    Q_UNUSED(row);
}

void MainWindow::onCellDouble(int row, int col)
{
    Q_UNUSED(col);
    playIndex(row);
}

void MainWindow::highlightPlayingRow()
{
    // сбрасываем выделение цветом
    for (int r = 0; r < m_table->rowCount(); ++r) {
        for (int c = 0; c < 5; ++c) {
            QTableWidgetItem *it = m_table->item(r, c);
            if (!it)
                continue;
            it->setData(Qt::BackgroundRole, QVariant());
        }
    }
    const QString cur = m_player->source().toLocalFile();
    if (cur.isEmpty())
        return;
    const int r = rowByPath(cur);
    if (r < 0)
        return;
    const QColor accent = palette().color(QPalette::Highlight);
    for (int c = 0; c < 5; ++c) {
        QTableWidgetItem *it = m_table->item(r, c);
        if (it)
            it->setData(Qt::BackgroundRole, QColor(accent.red(), accent.green(), accent.blue(), 60));
    }
}

void MainWindow::updateNowPlayingLabels()
{
    if (m_current < 0 || m_current >= m_tracks.size()) {
        m_lblTitle->setText(QStringLiteral("<нет трека>"));
        m_lblArtist->setText(QString());
        return;
    }
    const Track &t = m_tracks[m_current];
    m_lblTitle->setText(t.title);
    m_lblArtist->setText(t.artist);
    if (m_mini)
        m_mini->setTrack(t.title, t.artist, coverPixmap(t, 84));
}
