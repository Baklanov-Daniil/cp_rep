import asyncio
import os
import time
import wave
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

# Используем pyaudiowpatch для WASAPI loopback (Windows) или pyaudio (Linux)
try:
    import pyaudiowpatch as pyaudio
    print("✅ Используется pyaudiowpatch (WASAPI loopback)")
except ImportError:
    try:
        import pyaudio
        print("⚠️ Используется pyaudio")
    except ImportError:
        print("❌ PyAudio не установлен")
        pyaudio = None


class MeetingRecorder:
    """Бот для подключения к Яндекс.Телемост и записи звука"""
    
    def __init__(self, output_dir="recordings"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        self.playwright = None
        self.browser = None
        self.page = None
        self.is_recording = False
        self.audio_frames = []
        self.audio_stream = None
        self.pyaudio_instance = None
        self.sample_rate = 48000
        self.channels = 2
        self.recording_thread = None
    
    def get_loopback_device(self):
        """Найти устройство для записи системного звука"""
        if pyaudio is None:
            return None, None
        
        p = pyaudio.PyAudio()
        print("\n🔍 Поиск устройства для записи...")
        
        # WASAPI loopback (Windows)
        try:
            if hasattr(p, 'get_default_wasapi_loopback'):
                dev = p.get_default_wasapi_loopback()
                print(f"✅ WASAPI loopback: {dev['name']}")
                p.terminate()
                return dev['index'], int(dev['defaultSampleRate'])
        except Exception as e:
            print(f"️ WASAPI loopback не найден: {e}")
        
        # Ручной поиск
        for i in range(p.get_device_count()):
            try:
                dev = p.get_device_info_by_index(i)
                if dev.get('isLoopbackDevice', False) and dev['maxInputChannels'] > 0:
                    print(f"✅ Loopback: {dev['name']}")
                    p.terminate()
                    return dev['index'], int(dev['defaultSampleRate'])
            except:
                continue
        
        # По названию
        for i in range(p.get_device_count()):
            try:
                dev = p.get_device_info_by_index(i)
                name = dev['name'].lower()
                if ('loopback' in name or 'stereo mix' in name or 'стерео микшер' in name) and dev['maxInputChannels'] > 0:
                    print(f"✅ Найдено: {dev['name']}")
                    p.terminate()
                    return dev['index'], int(dev['defaultSampleRate'])
            except:
                continue
        
        # Fallback
        print("⚠️ Loopback не найден, используем дефолтное")
        try:
            default = p.get_default_input_device_info()
            p.terminate()
            return default['index'], int(default['defaultSampleRate'])
        except:
            p.terminate()
            return 0, 44100
    
    async def close_xdg_dialog(self):
        """Закрыть диалог xdg-open через xdotool (Linux/WSL)"""
        try:
            print("🔍 Поиск диалога xdg-open...")
            result = subprocess.run(
                ['xdotool', 'search', '--name', 'xdg-open'],
                capture_output=True, text=True, timeout=2
            )
            
            if result.stdout.strip():
                window_id = result.stdout.strip().split('\n')[0]
                print(f"✅ Найдено окно: {window_id}")
                subprocess.run(['xdotool', 'windowactivate', window_id], timeout=2)
                await asyncio.sleep(0.3)
                subprocess.run(['xdotool', 'key', 'Escape'], timeout=1)
                print("✅ Диалог закрыт через xdotool")
                return True
            else:
                print("️ Диалог xdg-open не найден")
                return False
        except subprocess.TimeoutExpired:
            print("⚠️ Таймаут xdotool")
            return False
        except FileNotFoundError:
            print("⚠️ xdotool не установлен. Установите: sudo apt install xdotool")
            return False
        except Exception as e:
            print(f"⚠️ Ошибка xdotool: {e}")
            return False
    
    async def connect_to_meeting(self, meeting_url: str, user_name: str = "AI Assistant"):
        """Подключиться к встрече в Телемосте"""
        print(f"\n🔗 Подключение к встрече: {meeting_url}")
        
        try:
            print("🌐 Запуск Playwright...")
            self.playwright = await async_playwright().start()
            
            print("🚀 Запуск браузера...")
            # ВАЖНО: аргументы для отключения диалогов
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=[
                    '--disable-features=ExternalProtocolDialog',
                    '--disable-default-apps',
                    '--no-first-run',
                ]
            )
            
            context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="ru-RU",
                permissions=[],
                ignore_https_errors=True
            )
            
            self.page = await context.new_page()
            
            # 🎯 ВАЖНО: Обработка ВСЕХ диалогов автоматически
            async def handle_dialog(dialog):
                print(f" Обнаружен диалог: {dialog.message}")
                await dialog.dismiss()  # Отклоняем (аналог Cancel)
                print("✅ Диалог закрыт через Playwright")
            
            self.page.on("dialog", handle_dialog)
            
            print(f"📍 Переход на {meeting_url}...")
            await self.page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
            print("✅ Страница загружена")
            
            # Ждём появления диалога и закрываем его
            await asyncio.sleep(3)
            
            # Нажимаем Escape несколько раз (на случай если диалог системный)
            for i in range(3):
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
                print(f"⌨️ Escape #{i+1} нажат")
            
            # Скриншот для отладки
            await self.page.screenshot(path="debug_after_dialog.png")
            print("📸 Скриншот сохранён")
            
            # Теперь ищем кнопку "Продолжить в браузере"
            print("🔍 Поиск кнопки 'Продолжить в браузере'...")
            continue_btn = None
            selectors = [
                'button:has-text("Продолжить в браузере")',
                'button:has-text("Continue in browser")',
                'button.green',
            ]
            
            for selector in selectors:
                try:
                    continue_btn = await self.page.wait_for_selector(selector, timeout=5000)
                    if continue_btn:
                        print(f"✅ Найдена: {selector}")
                        break
                except:
                    continue
            
            if continue_btn:
                await continue_btn.click()
                print("✅ Нажата 'Продолжить в браузере'")
                await asyncio.sleep(2)
                
                # Закрыть модальные окна
                for sel in ['button:has-text("Понятно")', '[data-testid="orb-button"]']:
                    try:
                        btn = await self.page.wait_for_selector(sel, timeout=1500)
                        if btn:
                            await btn.click(force=True)
                            print(f"✅ Закрыто: {sel}")
                            break
                    except:
                        continue
            
            # Кнопка "Подключиться"
            print("🔍 Поиск кнопки 'Подключиться'...")
            connect_button = None
            for selector in [
                'button:has-text("Подключиться")',
                'button:has-text("Join")',
                'button:has-text("Присоединиться")',
            ]:
                try:
                    connect_button = await self.page.wait_for_selector(selector, timeout=3000)
                    if connect_button:
                        print(f"✅ Найдена: {selector}")
                        break
                except:
                    continue
            
            if connect_button:
                name_input = await self.page.query_selector('input[type="text"]')
                if name_input:
                    await name_input.fill(user_name)
                    print(f"✍️ Имя: {user_name}")
                
                await connect_button.click(force=True)
                print("✅ Клик 'Подключиться'")
                
                print("⏳ Ожидание присоединения...")
                try:
                    await self.page.wait_for_selector(
                        'button:has-text("Покинуть"), button:has-text("Leave")',
                        timeout=15000
                    )
                    print("✅ Встреча загружена!")
                except:
                    print("⚠️ Таймаут, но продолжаем...")
                
                await asyncio.sleep(3)
                return True
            else:
                print("⚠️ Кнопка 'Подключиться' не найдена")
                await self.page.screenshot(path="debug_no_join.png", full_page=True)
                return False
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start_audio_recording(self):
        """Начать запись звука"""
        print("\n" + "="*60)
        print("️ НАЧАЛО ЗАПИСИ")
        print("="*60)
        
        if pyaudio is None:
            print("❌ PyAudio не установлен")
            return
        
        try:
            self.pyaudio_instance = pyaudio.PyAudio()
            device_index, sample_rate = self.get_loopback_device()
            
            if device_index is None:
                print("❌ Устройство не получено")
                return
            
            self.sample_rate = sample_rate if sample_rate else 48000
            self.channels = 2
            self.audio_frames = []
            self.is_recording = True
            
            CHUNK = 1024
            
            print(f"🔧 Device: {device_index}, Rate: {self.sample_rate}, Channels: {self.channels}")
            
            self.audio_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=CHUNK
            )
            
            self.recording_thread = threading.Thread(
                target=self._record_loop, args=(CHUNK,), daemon=True
            )
            self.recording_thread.start()
            print("✅ Запись запущена\n")
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            self.is_recording = False
    
    def _record_loop(self, chunk_size):
        try:
            while self.is_recording:
                data = self.audio_stream.read(chunk_size, exception_on_overflow=False)
                self.audio_frames.append(data)
        except Exception as e:
            if self.is_recording:
                print(f"❌ Ошибка записи: {e}")
        finally:
            self.is_recording = False
    
    def stop_audio_recording(self) -> str:
        print("\n" + "="*60)
        print("⏹️ ОСТАНОВКА ЗАПИСИ")
        print("="*60)
        
        self.is_recording = False
        
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2.0)
        
        time.sleep(0.3)
        
        stream = self.audio_stream
        pa = self.pyaudio_instance
        frames = self.audio_frames.copy()
        sample_rate = self.sample_rate
        channels = self.channels
        
        self.audio_stream = None
        self.pyaudio_instance = None
        self.audio_frames = []
        self.recording_thread = None
        
        if stream:
            try:
                stream.stop_stream()
                stream.close()
                print("✅ Stream закрыт")
            except Exception as e:
                print(f"⚠️ {e}")
        
        if pa:
            try:
                pa.terminate()
                print("✅ PyAudio terminated")
            except Exception as e:
                print(f"⚠️ {e}")
        
        if not frames:
            print("❌ Нет данных")
            return None
        
        total_bytes = sum(len(f) for f in frames)
        duration = total_bytes / (sample_rate * channels * 2)
        print(f" {total_bytes/1024:.1f} KB, ~{duration:.1f} сек")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.output_dir / f"telemost_{timestamp}.wav"
        
        try:
            with wave.open(str(filepath), 'wb') as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(b''.join(frames))
            
            size = filepath.stat().st_size
            print(f"✅ Сохранено: {filepath}")
            print(f"   📦 {size/1024:.1f} KB")
            print("="*60 + "\n")
            return str(filepath)
        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")
            return None
    
    async def disconnect(self):
        print("👋 Отключение...")
        
        if self.page:
            try:
                leave = await self.page.wait_for_selector(
                    'button:has-text("Покинуть"), button:has-text("Leave")',
                    timeout=5000
                )
                await leave.click()
                print("✅ Отключились")
            except:
                print("⚠️ Кнопка выхода не найдена")
        
        if self.browser:
            try:
                await self.browser.close()
                print("🌐 Браузер закрыт")
            except:
                pass
        
        if self.playwright:
            try:
                await self.playwright.stop()
                print("✅ Playwright остановлен")
            except:
                pass
    
    async def connect_and_record(self, meeting_url: str, duration_minutes: int = 10) -> str:
        print(f"\n📹 Запись (макс. {duration_minutes} мин)")
        print(f"🔗 {meeting_url}")
        
        connected = await self.connect_to_meeting(meeting_url)
        if not connected:
            print("❌ Не удалось подключиться")
            return None
        
        self.start_audio_recording()
        print("⏳ Запись идёт...")
        
        start_time = asyncio.get_event_loop().time()
        max_duration = duration_minutes * 60
        
        try:
            while True:
                if not self.page or self.page.is_closed():
                    print("✅ Страница закрыта")
                    break
                
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= max_duration:
                    print(f"⏰ Лимит ({duration_minutes} мин)")
                    break
                
                try:
                    url = self.page.url
                    if 'telemost.yandex.ru' not in url or '/j/' not in url:
                        print("✅ URL изменился")
                        break
                except:
                    pass
                
                if int(elapsed) % 30 == 0:
                    print(f"   ️ {int(elapsed)} сек, кадров: {len(self.audio_frames)}")
                
                await asyncio.sleep(5)
        except Exception as e:
            print(f"⚠️ Ошибка мониторинга: {e}")
        
        filepath = self.stop_audio_recording()
        await self.disconnect()
        return filepath
