cmake_minimum_required(VERSION 3.21)
project(HoxMusicPlayer VERSION 0.5.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

find_package(Qt6 REQUIRED COMPONENTS Widgets Multimedia Network Concurrent)

# ---------- HoxPlayer ----------
add_executable(HoxPlayer WIN32
    src/common/version.h
    src/player/main.cpp
    src/player/mainwindow.h
    src/player/mainwindow.cpp
    src/player/mainwindow_play.cpp
    src/player/karaoke.h
    src/player/karaoke.cpp
    src/player/miniplayer.h
    src/player/miniplayer.cpp
    src/player/visualizer.h
    src/player/visualizer.cpp
    src/player/whatsnew.h
    res/resources.qrc
)
target_link_libraries(HoxPlayer PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network)
target_include_directories(HoxPlayer PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

if(WIN32)
    target_sources(HoxPlayer PRIVATE res/app.rc)
endif()

# ---------- HoxLauncher ----------
add_executable(HoxLauncher WIN32
    src/common/version.h
    src/launcher/main.cpp
    res/resources.qrc
)
target_link_libraries(HoxLauncher PRIVATE Qt6::Widgets Qt6::Network Qt6::Concurrent shell32)
target_include_directories(HoxLauncher PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

if(WIN32)
    target_sources(HoxLauncher PRIVATE res/app.rc)
endif()
