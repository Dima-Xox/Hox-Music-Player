#include "version.h"
#include "mainwindow.h"

#include <QApplication>
#include <QSplashScreen>
#include <QPixmap>
#include <QPainter>
#include <QPainterPath>
#include <QTimer>
#include <QLocalServer>
#include <QLocalSocket>
#include <QCommandLineParser>
#include <QDebug>
#include <QStyleFactory>

// Splash: логотип рисуется программно (тёмный фон, «Hox», версия).
static QPixmap makeSplash()
{
    QPixmap pm(560, 340);
    pm.fill(Qt::transparent);
    QPainter p(&pm);
    p.setRenderHint(QPainter::Antialiasing);
    QLinearGradient g(0, 0, pm.width(), pm.height());
    g.setColorAt(0.0, QColor(0x0b, 0x10, 0x20));
    g.setColorAt(1.0, QColor(0x14, 0x1f, 0x3a));
    QPainterPath path;
    path.addRoundedRect(pm.rect(), 14, 14);
    p.fillPath(path, g);

    QFont f;
    f.setPixelSize(88);
    f.setBold(true);
    p.setFont(f);
    p.setPen(QColor(0xe8, 0xec, 0xf5));
    p.drawText(pm.rect().adjusted(0, -40, 0, -40), Qt::AlignHCenter, QStringLiteral("Hox"));
    p.setPen(QColor(0x4f, 0xc3, 0xf7));
    p.drawRoundedRect(pm.rect().center().x() - 60, 178, 120, 4, 2, 2);
    QFont f2;
    f2.setPixelSize(15);
    p.setFont(f2);
    p.setPen(QColor(0x8f, 0x9c, 0xb8));
    p.drawText(pm.rect().adjusted(0, 60, 0, 60), Qt::AlignHCenter, QStringLiteral("MUSIC PLAYER"));
    QFont f3;
    f3.setPixelSize(12);
    p.setFont(f3);
    p.drawText(pm.rect().adjusted(0, 120, 0, 110), Qt::AlignHCenter,
               QStringLiteral("v") + QStringLiteral(APP_VERSION));
    return pm;
}

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    QApplication::setOrganizationName(QStringLiteral("Hox"));
    QApplication::setApplicationName(QStringLiteral("HoxPlayer"));
    QApplication::setApplicationVersion(QStringLiteral(APP_VERSION));
    QApplication::setWindowIcon(QIcon(QStringLiteral(":/app.ico")));
    app.setQuitOnLastWindowClosed(true);

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Hox Music Player — лёгкий плеер с караоке"));
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption selfcheckOpt(QStringLiteral("selfcheck"),
                                    QStringLiteral("Проверка запуска (для CI): создать окно и выйти"));
    QCommandLineOption pathsOpt(QStringLiteral("open"), QStringLiteral("Открыть файлы"),
                                QStringLiteral("files"));
    parser.addOption(selfcheckOpt);
    parser.addOption(pathsOpt);
    parser.addPositionalArgument(QStringLiteral("files"), QStringLiteral("Аудиофайлы"));
    parser.process(app);

    // одиночный экземпляр: второй запуск передаёт файлы первому
    QLocalSocket sock;
    sock.connectToServer(QStringLiteral("HoxPlayerInstance"));
    if (sock.waitForConnected(300)) {
        const QStringList args = parser.positionalArguments();
        if (!args.isEmpty()) {
            sock.write(args.join(QLatin1Char('\n')).toUtf8());
            sock.flush();
            sock.waitForBytesWritten(300);
        }
        return 0;
    }

    QLocalServer::removeServer(QStringLiteral("HoxPlayerInstance"));
    QLocalServer *srv = new QLocalServer(&app);
    if (!srv->listen(QStringLiteral("HoxPlayerInstance")))
        qWarning("HoxPlayer: не удалось поднять сервер одиночного экземпляра");

    MainWindow *win = nullptr;
    if (parser.isSet(selfcheckOpt)) {
        win = new MainWindow(true);
    } else {
        QSplashScreen splash(makeSplash());
        splash.show();
        QApplication::processEvents();
        win = new MainWindow(false);
        splash.finish(win);
    }

    // приём файлов от второго экземпляра
    QObject::connect(srv, &QLocalServer::newConnection, &app, [srv, win]() {
        while (QLocalSocket *c = srv->nextPendingConnection()) {
            QObject::connect(c, &QLocalSocket::disconnected, c, &QLocalSocket::deleteLater);
            QObject::connect(c, &QLocalSocket::readyRead, c, [c, win]() {
                const QStringList paths =
                    QString::fromUtf8(c->readAll()).split(QLatin1Char('\n'), Qt::SkipEmptyParts);
                if (win && !paths.isEmpty()) {
                    win->show();
                    win->raise();
                    win->activateWindow();
                    win->startPlaybackFromArgs(paths);
                }
            });
        }
    });

    // файлы из командной строки
    const QStringList posArgs = parser.positionalArguments();
    if (!posArgs.isEmpty() && win)
        win->startPlaybackFromArgs(posArgs);

    const int rc = app.exec();
    QLocalServer::removeServer(QStringLiteral("HoxPlayerInstance"));
    return rc;
}
