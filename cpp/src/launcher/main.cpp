// HoxLauncher — менеджер плеера (C++/Qt 6).
// Возможности: плеер скачивается и устанавливается С GitHub под управлением
// лаунчера: проверка обновлений с GitHub Releases, скачивание установщика
// с проверкой SHA-256, тихая установка с правами администратора (UAC),
// докачка плеера, если его нет, запуск плеера, одиночный экземпляр, трей.
#include "version.h"

#include <QApplication>
#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QProgressBar>
#include <QPlainTextEdit>
#include <QPainter>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QNetworkAccessManager>
#include <QNetworkRequest>
#include <QNetworkReply>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QSettings>
#include <QSystemTrayIcon>
#include <QMenu>
#include <QCloseEvent>
#include <QLocalServer>
#include <QLocalSocket>
#include <QCommandLineParser>
#include <QCryptographicHash>
#include <QProcess>
#include <QRegularExpression>
#include <QFile>
#include <QFileInfo>
#include <QDir>
#include <QTimer>
#include <QDebug>
#include <QFutureWatcher>
#include <QtConcurrent>

#ifdef Q_OS_WIN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>
#endif

static constexpr const char *kReleasesApi =
    "https://api.github.com/repos/" GITHUB_REPO "/releases/latest";

// Глобальные указатели: сервер одиночного экземпляра нужно закрыть перед
// перезапуском лаунчера после тихого обновления, окно — развернуть при
// повторном запуске.
class LauncherWindow;
static QLocalServer  *g_instanceServer  = nullptr;
static LauncherWindow *g_launcherWindow = nullptr;

static int parseVersion(const QString &v)
{
    // «0.5.0» -> 500, «0.4.10» -> 410 (числовое сравнение по старшинству)
    const QStringList parts = v.trimmed().remove(QLatin1Char('v')).split(QLatin1Char('.'));
    int res = 0;
    for (int i = 0; i < parts.size(); ++i)
        res = res * 100 + parts[i].toInt() * (i == 0 ? 1 : 1);
    return res;
}

class LauncherWindow : public QWidget {
    Q_OBJECT
public:
    LauncherWindow() : m_net(new QNetworkAccessManager(this))
    {
        setWindowTitle(QStringLiteral("Hox — лаунчер v") + QStringLiteral(APP_VERSION));
        setWindowIcon(QIcon(QStringLiteral(":/app.ico")));
        setFixedSize(560, 430);

        auto *logo = new QLabel(this);
        logo->setAlignment(Qt::AlignCenter);
        {
            QPixmap pm(520, 90);
            pm.fill(Qt::transparent);
            QPainter p(&pm);
            QFont f;
            f.setPixelSize(46);
            f.setBold(true);
            p.setFont(f);
            p.setPen(palette().color(QPalette::WindowText));
            p.drawText(pm.rect(), Qt::AlignVCenter | Qt::AlignLeft, QStringLiteral("Hox"));
            QFont f2;
            f2.setPixelSize(14);
            p.setFont(f2);
            p.setPen(palette().color(QPalette::Mid));
            p.drawText(pm.rect().adjusted(140, 0, 0, 0), Qt::AlignVCenter | Qt::AlignLeft,
                       QStringLiteral("MUSIC PLAYER — лаунчер\nРанняя бета"));
            logo->setPixmap(pm);
        }

        m_status = new QLabel(this);
        m_status->setWordWrap(true);

        m_log = new QPlainTextEdit(this);
        m_log->setReadOnly(true);
        m_log->setMaximumBlockCount(500);

        m_progress = new QProgressBar(this);
        m_progress->setRange(0, 100);
        m_progress->setValue(0);
        m_progress->hide();

        m_btnPlay = new QPushButton(QStringLiteral("Запустить плеер"), this);
        m_btnCheck = new QPushButton(QStringLiteral("Проверить обновления"), this);
        m_btnUpdate = new QPushButton(this);
        m_btnUpdate->hide();

        auto *row = new QHBoxLayout;
        row->addWidget(m_btnPlay);
        row->addWidget(m_btnCheck);
        row->addStretch(1);

        auto *lay = new QVBoxLayout(this);
        lay->setContentsMargins(20, 16, 20, 16);
        lay->setSpacing(10);
        lay->addWidget(logo);
        lay->addWidget(m_status);
        lay->addWidget(m_btnUpdate);
        lay->addLayout(row);
        lay->addWidget(m_progress);
        lay->addWidget(m_log, 1);

        connect(m_btnPlay, &QPushButton::clicked, this, &LauncherWindow::launchPlayer);
        connect(m_btnCheck, &QPushButton::clicked, this, &LauncherWindow::checkUpdates);
        connect(m_btnUpdate, &QPushButton::clicked, this, &LauncherWindow::downloadUpdate);

        // трей
        m_tray = new QSystemTrayIcon(windowIcon(), this);
        auto *trayMenu = new QMenu(this);
        trayMenu->addAction(QStringLiteral("Показать окно"), this, &LauncherWindow::showNormal);
        trayMenu->addAction(QStringLiteral("Запустить плеер"), this, &LauncherWindow::launchPlayer);
        trayMenu->addSeparator();
        trayMenu->addAction(QStringLiteral("Выход"), qApp, &QApplication::quit);
        m_tray->setContextMenu(trayMenu);
        m_tray->setToolTip(QStringLiteral("Hox — лаунчер"));
        connect(m_tray, &QSystemTrayIcon::activated, this,
                [this](QSystemTrayIcon::ActivationReason r) {
                    if (r == QSystemTrayIcon::Trigger || r == QSystemTrayIcon::DoubleClick)
                        showNormal();
                });
        m_tray->show();

        log(QStringLiteral("Лаунчер v") + QStringLiteral(APP_VERSION));
        refreshStatusLine();
        QTimer::singleShot(400, this, &LauncherWindow::checkUpdates);
    }

protected:
    void closeEvent(QCloseEvent *e) override
    {
        // крестик не закрывает лаунчер — он остаётся в трее
        if (m_tray->isVisible()) {
            hide();
            if (!m_trayHintShown) {
                m_tray->showMessage(QStringLiteral("Hox"),
                                    QStringLiteral("Лаунчер свернут в трей — музыка под рукой"),
                                    QSystemTrayIcon::Information, 2500);
                m_trayHintShown = true;
            }
            e->ignore();
            return;
        }
        e->accept();
    }

private slots:
    void refreshStatusLine()
    {
        if (QFileInfo::exists(playerPath())) {
            const QString v = m_playerVersion.isEmpty()
                                  ? QString() : QStringLiteral(" — v") + m_playerVersion;
            m_status->setText(QStringLiteral("Плеер установлен%1. Обновления лаунчер проверяет сам.").arg(v));
            m_btnPlay->setEnabled(true);
        } else {
            m_status->setText(QStringLiteral(
                "Плеер не найден рядом с лаунчером (HoxPlayer.exe).\n"
                "Нажмите «Скачать плеер с GitHub» — лаунчер сам скачает последнюю версию "
                "с GitHub Releases и тихо установит её."));
            m_btnPlay->setEnabled(false);
            m_btnUpdate->setText(QStringLiteral("Скачать плеер с GitHub"));
            m_btnUpdate->show();
        }
    }

    void launchPlayer()
    {
        const QString exe = playerPath();
        if (!QFileInfo::exists(exe)) {
            log(QStringLiteral("Плеер не найден: ") + exe);
            return;
        }
        QProcess::startDetached(exe, QStringList());
        log(QStringLiteral("Запуск: ") + exe);
        hide();   // окно уходит в трей, пока плеер играет
    }

    void checkUpdates()
    {
        if (m_checkBusy)
            return;
        m_checkBusy = true;
        m_btnCheck->setEnabled(false);
        log(QStringLiteral("Проверяю обновления…"));

        QNetworkRequest req{ QUrl(QString::fromLatin1(kReleasesApi)) };
        req.setRawHeader(QByteArray("User-Agent"), QByteArray("Hox-Launcher/") + APP_VERSION);
        req.setRawHeader(QByteArray("Accept"), QByteArray("application/vnd.github+json"));
        QNetworkReply *rep = m_net->get(req);
        connect(rep, &QNetworkReply::finished, this, [this, rep]() {
            m_checkBusy = false;
            m_btnCheck->setEnabled(true);
            rep->deleteLater();
            if (rep->error() != QNetworkReply::NoError) {
                log(QStringLiteral("Проверка обновлений: ошибка сети (")
                    + rep->errorString() + QStringLiteral(")"));
                return;
            }
            const QJsonObject rel = QJsonDocument::fromJson(rep->readAll()).object();
            const QString tag = rel.value(QStringLiteral("tag_name")).toString();
            const QString notes = rel.value(QStringLiteral("body")).toString();
            if (tag.isEmpty()) {
                log(QStringLiteral("Не удалось прочитать ответ GitHub"));
                return;
            }
            m_remoteTag = tag;
            m_remoteNotes = notes;

            // версия ИМЕННО плеера: лаунчер и плеер выходят вместе, но плеер
            // могли удалить или заменить отдельно от лаунчера
            const bool havePlayer = QFileInfo::exists(playerPath());
            QString playerVer;
            if (havePlayer) {
                playerVer = detectPlayerVersion();
                m_playerVersion = playerVer;
            }
            if (havePlayer) {
                const QString v = playerVer.isEmpty() ? QString()
                                                      : QStringLiteral(" — v") + playerVer;
                m_status->setText(QStringLiteral("Плеер установлен%1.").arg(v));
                m_btnPlay->setEnabled(true);
            } else {
                m_status->setText(QStringLiteral("Плеер не найден — скачается с GitHub."));
                m_btnPlay->setEnabled(false);
            }

            // с чем сравнивать релиз: с версией плеера; если её узнать не
            // удалось — с версией лаунчера (они всегда выходят вместе)
            const QString base = havePlayer ? (playerVer.isEmpty() ? QStringLiteral(APP_VERSION)
                                                                   : playerVer)
                                            : QStringLiteral("0.0.0");
            const QString relVer = tag.startsWith(QLatin1Char('v')) ? tag.mid(1) : tag;
            if (parseVersion(tag) > parseVersion(base)) {
                m_btnUpdate->setText(havePlayer
                    ? QStringLiteral("Обновить плеер до v") + relVer
                    : QStringLiteral("Скачать плеер v") + relVer + QStringLiteral(" с GitHub"));
                m_btnUpdate->show();
                log(QStringLiteral("Доступна новая версия: ") + tag
                    + QStringLiteral(" — нажмите «Обновить»"));
                if (!notes.isEmpty())
                    log(QStringLiteral("Изменения:\n") + notes.left(600));
            } else if (!havePlayer) {
                m_btnUpdate->setText(QStringLiteral("Скачать плеер v") + relVer
                                     + QStringLiteral(" с GitHub"));
                m_btnUpdate->show();
                log(QStringLiteral("Плеер не установлен — можно скачать релиз ") + tag);
            } else {
                m_btnUpdate->hide();
                log(QStringLiteral("У вас самая свежая версия (плеер v")
                    + (playerVer.isEmpty() ? QStringLiteral(APP_VERSION) : playerVer)
                    + QStringLiteral("), релиз на GitHub: ") + tag);
            }
        });
    }

    void downloadUpdate()
    {
        if (m_remoteTag.isEmpty() || m_busy)
            return;
        m_busy = true;
        m_btnUpdate->setEnabled(false);
        m_progress->show();

        // ищем ассеты установщика
        QNetworkRequest req{ QUrl(QString::fromLatin1(kReleasesApi)) };
        req.setRawHeader(QByteArray("User-Agent"), QByteArray("Hox-Launcher/") + APP_VERSION);
        req.setRawHeader(QByteArray("Accept"), QByteArray("application/vnd.github+json"));
        QNetworkReply *rep = m_net->get(req);
        connect(rep, &QNetworkReply::finished, this, [this, rep]() {
            rep->deleteLater();
            if (rep->error() != QNetworkReply::NoError) {
                failUpdate(QStringLiteral("Ошибка сети: ") + rep->errorString());
                return;
            }
            const QJsonObject rel = QJsonDocument::fromJson(rep->readAll()).object();
            if (m_remoteTag.isEmpty())   // кнопку нажали до завершения проверки
                m_remoteTag = rel.value(QStringLiteral("tag_name")).toString();
            const QJsonArray assets = rel.value(QStringLiteral("assets")).toArray();
            QString setupUrl, shaUrl;
            for (const QJsonValue &v : assets) {
                const QJsonObject a = v.toObject();
                const QString name = a.value(QStringLiteral("name")).toString();
                const QString url = a.value(QStringLiteral("browser_download_url")).toString();
                if (name.startsWith(QStringLiteral("Hox-Setup-")) && name.endsWith(QStringLiteral(".exe"))
                    && !name.contains(QStringLiteral("Win7"))) {
                    setupUrl = url;
                } else if (name.startsWith(QStringLiteral("Hox-Setup-")) && name.endsWith(QStringLiteral(".sha256"))
                           && !name.contains(QStringLiteral("Win7"))) {
                    shaUrl = url;
                }
            }
            if (setupUrl.isEmpty()) {
                failUpdate(QStringLiteral("В релизе нет установщика Hox-Setup-*.exe"));
                return;
            }
            log(QStringLiteral("Скачиваю установщик…"));
            downloadFile(QUrl(setupUrl), [this, shaUrl](const QByteArray &data, const QString &err) {
                if (!err.isEmpty()) {
                    failUpdate(err);
                    return;
                }
                if (shaUrl.isEmpty()) {
                    log(QStringLiteral("Сайдкар .sha256 не найден — пропускаю проверку суммы"));
                    installUpdate(data, QByteArray());
                    return;
                }
                downloadFile(QUrl(shaUrl), [this, data](const QByteArray &sha, const QString &err2) {
                    installUpdate(data, err2.isEmpty() ? sha : QByteArray());
                });
            });
        });
    }

private:
    // Версия установленного плеера: спрашиваем сам HoxPlayer.exe (--version),
    // при неудаче — запись установщика в реестре. Пусто — узнать не удалось.
    QString detectPlayerVersion() const
    {
        const QString exe = playerPath();
        if (!QFileInfo::exists(exe))
            return QString();
        QProcess p;
        p.start(exe, QStringList() << QStringLiteral("--version"));
        if (p.waitForStarted(2000) && p.waitForFinished(5000)) {
            static const QRegularExpression rxVer(QStringLiteral("\\d+\\.\\d+\\.\\d+"));
            const auto m = rxVer.match(QString::fromUtf8(p.readAllStandardOutput()));
            if (m.hasMatch())
                return m.captured(0);
        }
        QSettings reg(QStringLiteral(
            "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Hoxmusicp"),
            QSettings::NativeFormat);
        return reg.value(QStringLiteral("DisplayVersion")).toString().trimmed();
    }

public:
    // Запуск после тихого обновления: окно не показываем — сворачиваемся
    // в трей и стартуем обновлённый плеер.
    void startAfterUpdate()
    {
        log(QStringLiteral("Обновление установлено — запускаю плеер…"));
        m_tray->showMessage(QStringLiteral("Hox"),
                            QStringLiteral("Плеер обновлён — запускаю"),
                            QSystemTrayIcon::Information, 3000);
        QTimer::singleShot(500, this, &LauncherWindow::launchPlayer);
        checkUpdates();
    }

private:
    QString playerPath() const
    {
        return QDir::cleanPath(QCoreApplication::applicationDirPath() + QStringLiteral("/HoxPlayer.exe"));
    }

    void log(const QString &s)
    {
        m_log->appendPlainText(s);
    }

    void failUpdate(const QString &why)
    {
        m_busy = false;
        m_btnUpdate->setEnabled(true);
        m_progress->hide();
        log(QStringLiteral("Обновление не выполнено: ") + why);
    }

    template <typename Then>
    void downloadFile(const QUrl &url, Then then)
    {
        QNetworkRequest req(url);
        req.setRawHeader(QByteArray("User-Agent"), QByteArray("Hox-Launcher/") + APP_VERSION);
        QNetworkReply *rep = m_net->get(req);
        connect(rep, &QNetworkReply::downloadProgress, this, [this](qint64 done, qint64 total) {
            if (total > 0)
                m_progress->setValue(int(done * 100 / total));
        });
        connect(rep, &QNetworkReply::finished, this, [this, rep, then]() {
            rep->deleteLater();
            m_progress->hide();
            if (rep->error() != QNetworkReply::NoError) {
                then(QByteArray(), QStringLiteral("Ошибка скачивания: ") + rep->errorString());
                return;
            }
            then(rep->readAll(), QString());
        });
    }

    void installUpdate(const QByteArray &exeData, const QByteArray &shaSidecar)
    {
        // проверка SHA-256
        if (!shaSidecar.isEmpty()) {
            const QString expected =
                QString::fromLatin1(shaSidecar).section(QLatin1Char(' '), 0, 0).trimmed();
            const QByteArray actual =
                QCryptographicHash::hash(exeData, QCryptographicHash::Sha256).toHex();
            if (expected.compare(QString::fromLatin1(actual), Qt::CaseInsensitive) != 0) {
                failUpdate(QStringLiteral("SHA-256 не совпал — файл повреждён"));
                return;
            }
            log(QStringLiteral("SHA-256 совпал"));
        }
        // сохранить во временную папку
        const QString tmp = QStandardPaths::writableLocation(QStandardPaths::TempLocation);
        const QString setupPath = tmp + QStringLiteral("/Hox-Setup-")
                                  + m_remoteTag.mid(1) + QStringLiteral(".exe");
        QFile f(setupPath);
        if (!f.open(QIODevice::WriteOnly) || f.write(exeData) != exeData.size()) {
            failUpdate(QStringLiteral("Не удалось сохранить ") + setupPath);
            return;
        }
        f.close();
        log(QStringLiteral("Запускаю тихую установку (запрос прав администратора)…"));

        // установка требует UAC — запускаем через runas и ждём в отдельном потоке
        const std::wstring file = QDir::toNativeSeparators(setupPath).toStdWString();
        const std::wstring params =
            (QStringLiteral("/S /D=")
             + QDir::toNativeSeparators(installBase())).toStdWString();
        auto watcher = new QFutureWatcher<bool>(this);
        connect(watcher, &QFutureWatcher<bool>::finished, this, [this, watcher]() {
            watcher->deleteLater();
            m_busy = false;
            m_btnUpdate->setEnabled(true);
            m_progress->hide();
            if (watcher->result()) {
                // Обычно сюда не дойти: установщик на старте убивает лаунчер
                // taskkill'ом и сам перезапускает его с --after-update. Ветка
                // нужна, если taskkill не нашёл наш процесс.
                log(QStringLiteral("Обновление установлено. Перезапускаю лаунчер…"));
                if (g_instanceServer)
                    g_instanceServer->close();
                QLocalServer::removeServer(QStringLiteral("HoxLauncherInstance"));
                QProcess::startDetached(QCoreApplication::applicationFilePath(),
                                        QStringList() << QStringLiteral("--after-update"));
                qApp->quit();
            } else {
                log(QStringLiteral("Установка не выполнена (UAC отменён или ошибка)."));
            }
        });
        watcher->setFuture(QtConcurrent::run([file, params]() -> bool {
#ifdef Q_OS_WIN
            SHELLEXECUTEINFOW sei{};
            sei.cbSize = sizeof(sei);
            sei.lpVerb = L"runas";
            sei.lpFile = file.c_str();
            sei.lpParameters = params.c_str();
            sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_DDEWAIT;
            sei.nShow = SW_HIDE;
            if (!ShellExecuteExW(&sei))
                return false;
            if (sei.hProcess) {
                WaitForSingleObject(sei.hProcess, 30 * 60 * 1000);
                CloseHandle(sei.hProcess);
            }
            return true;
#else
            return false;
#endif
        }));
    }

    QString installBase() const
    {
        // bin лаунчера -> базовый каталог установки
        return QDir::cleanPath(QCoreApplication::applicationDirPath() + QStringLiteral("/.."));
    }

    QNetworkAccessManager *m_net = nullptr;
    QLabel *m_status = nullptr;
    QPlainTextEdit *m_log = nullptr;
    QProgressBar *m_progress = nullptr;
    QPushButton *m_btnPlay = nullptr;
    QPushButton *m_btnCheck = nullptr;
    QPushButton *m_btnUpdate = nullptr;
    QSystemTrayIcon *m_tray = nullptr;
    QString m_remoteTag;
    QString m_remoteNotes;
    QString m_playerVersion;   // версия установленного плеера (если известна)
    bool m_busy = false;
    bool m_checkBusy = false;
    bool m_trayHintShown = false;
};

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    QApplication::setOrganizationName(QStringLiteral("Hox"));
    QApplication::setApplicationName(QStringLiteral("HoxLauncher"));
    QApplication::setApplicationVersion(QStringLiteral(APP_VERSION));
    QApplication::setWindowIcon(QIcon(QStringLiteral(":/app.ico")));

    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption selfcheckOpt(QStringLiteral("selfcheck"),
                                    QStringLiteral("Проверка запуска (для CI)"));
    QCommandLineOption afterUpdateOpt(QStringLiteral("after-update"),
                                      QStringLiteral("Запуск после тихого обновления: в трей и старт плеера"));
    parser.addOption(selfcheckOpt);
    parser.addOption(afterUpdateOpt);
    parser.process(app);

    // одиночный экземпляр: повторный запуск разворачивает открытое окно
    QLocalSocket sock;
    sock.connectToServer(QStringLiteral("HoxLauncherInstance"));
    if (sock.waitForConnected(300)) {
        sock.write(QByteArrayLiteral("show\n"));
        sock.flush();
        sock.waitForBytesWritten(300);
        return 0;
    }

    QLocalServer::removeServer(QStringLiteral("HoxLauncherInstance"));
    g_instanceServer = new QLocalServer(&app);
    g_instanceServer->listen(QStringLiteral("HoxLauncherInstance"));

    if (parser.isSet(selfcheckOpt)) {
        qInfo("HoxLauncher selfcheck OK %s", APP_VERSION);
        QTimer::singleShot(300, &app, &QApplication::quit);
        return app.exec();
    }

    // команда «show» от второго экземпляра — развернуть окно
    QObject::connect(g_instanceServer, &QLocalServer::newConnection, &app, []() {
        while (QLocalSocket *c = g_instanceServer->nextPendingConnection()) {
            QObject::connect(c, &QLocalSocket::disconnected, c, &QLocalSocket::deleteLater);
            QObject::connect(c, &QLocalSocket::readyRead, c, [c]() {
                if (QString::fromUtf8(c->readAll()).contains(QLatin1String("show"))
                    && g_launcherWindow)
                    g_launcherWindow->showNormal();
            });
        }
    });

    LauncherWindow w;
    g_launcherWindow = &w;
    w.show();
    if (parser.isSet(afterUpdateOpt))
        w.startAfterUpdate();
    const int rc = app.exec();
    QLocalServer::removeServer(QStringLiteral("HoxLauncherInstance"));
    return rc;
}

#include "main.moc"
