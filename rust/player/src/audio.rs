//! Аудио-движок: rodio (WASAPI) + symphonia-декодеры.
//! Порт поведения QMediaPlayer: play/pause/seek/volume, позиция, конец трека.

use std::io::BufReader;
use std::path::Path;
use std::time::Duration;

use rodio::Decoder;
use rodio::source::Source;

pub struct AudioEngine {
    _stream: Option<rodio::OutputStream>,
    handle: Option<rodio::OutputStreamHandle>,
    sink: Option<rodio::Sink>,
    volume: f32,
}

impl AudioEngine {
    pub fn new() -> Result<AudioEngine, String> {
        let (stream, handle) = rodio::OutputStream::try_default()
            .map_err(|e| format!("аудиовыход недоступен: {e}"))?;
        Ok(AudioEngine {
            _stream: Some(stream),
            handle: Some(handle),
            sink: None,
            volume: 0.8,
        })
    }

    /// Движок без звука (когда аудиоустройство недоступно) — UI остаётся живым.
    pub fn dummy() -> AudioEngine {
        AudioEngine { _stream: None, handle: None, sink: None, volume: 0.8 }
    }

    /// Загрузить файл и начать играть с нуля.
    pub fn play_file(&mut self, path: &str) -> Result<(), String> {
        let handle = self
            .handle
            .as_ref()
            .ok_or_else(|| "аудиовыход недоступен".to_string())?;
        let file =
            std::fs::File::open(Path::new(path)).map_err(|e| format!("не открыть файл: {e}"))?;
        let decoder = Decoder::new(BufReader::new(file))
            .map_err(|e| format!("формат не поддерживается: {e}"))?;
        let sink =
            rodio::Sink::try_new(handle).map_err(|e| format!("аудио-поток: {e}"))?;
        sink.set_volume(self.volume);
        sink.append(decoder);
        self.sink.replace(sink);
        Ok(())
    }

    fn sink(&self) -> Option<&rodio::Sink> {
        self.sink.as_ref().filter(|s| !s.empty())
    }

    pub fn pause(&mut self) {
        if let Some(s) = self.sink() {
            s.pause();
        }
    }

    pub fn resume(&mut self) {
        if let Some(s) = self.sink() {
            s.play();
        }
    }

    pub fn stop(&mut self) {
        if let Some(s) = self.sink.take() {
            s.stop();
        }
    }

    pub fn is_playing(&self) -> bool {
        match self.sink() {
            Some(s) => !s.is_paused(),
            None => false,
        }
    }

    /// Трек доиграл до конца (sink опустел).
    pub fn finished(&self) -> bool {
        match &self.sink {
            Some(s) => s.empty(),
            None => false,
        }
    }

    pub fn position_ms(&self) -> i64 {
        self.sink()
            .map(|s| s.get_pos().as_millis() as i64)
            .unwrap_or(0)
    }

    pub fn set_volume(&mut self, v: f32) {
        self.volume = v.clamp(0.0, 1.0);
        if let Some(s) = &self.sink {
            s.set_volume(self.volume);
        }
    }

    pub fn volume(&self) -> f32 {
        self.volume
    }

    pub fn seek_ms(&mut self, pos_ms: i64) {
        let pos = Duration::from_millis(pos_ms.max(0) as u64);
        if let Some(s) = self.sink() {
            let _ = s.try_seek(pos);
        }
    }
}
