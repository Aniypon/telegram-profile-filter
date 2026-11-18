import asyncio
import hashlib
import os
import platform
import random
import string
import subprocess
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from loguru import logger


async def update_stat(worker):
    """Кроссплатформенное обновление статистики"""
    try:
        # Используем ThreadPoolExecutor для неблокирующего выполнения
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            title_text = f"QueVinchik - Подходящие {worker.suitable_forms} | Скип {worker.skipped_forms} | Всего {worker.all_forms}"
            
            # Проверяем операционную систему
            if platform.system() == "Windows":
                # Windows: используем команду title
                await loop.run_in_executor(executor, os.system, f'title "{title_text}"')
            else:
                # Unix/Linux/macOS: используем escape-последовательности для изменения заголовка терминала
                await loop.run_in_executor(
                    executor, 
                    lambda: print(f'\033]0;{title_text}\007', end='', flush=True)
                )
    except Exception as e:
        logger.debug(f"Ошибка обновления статистики: {e}")


def notify(worker):
    """Кроссплатформенные уведомления"""
    if worker.config["search"]["forms"]["notify"]["move_on_top"]:
        try:
            system = platform.system()
            if system == "Linux":
                # Linux: используем xdotool если доступен
                subprocess.Popen(["xdotool", "getactivewindow"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            elif system == "Darwin":  # macOS
                # macOS: используем osascript для активации окна терминала
                subprocess.Popen([
                    "osascript", "-e", 
                    'tell application "Terminal" to activate'
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            elif system == "Windows":
                # Windows: используем PowerShell для активации окна
                subprocess.Popen([
                    "powershell", "-Command",
                    "[Microsoft.VisualBasic.Interaction]::AppActivate((Get-Process -Id $PID).MainWindowTitle)"
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        except Exception as e:
            logger.debug(f"Ошибка уведомления: {e}")


def setup_logger():
    """Настройка логгера с оптимизацией"""
    logger.add(
        sys.stdout, 
        colorize=True, 
        enqueue=True, 
        backtrace=True, 
        diagnose=True,
        # Уменьшаем уровень для production
        level="INFO"
    )
    logger.remove(0)


def hash_form_result(form):
    """Оптимизированное хеширование"""
    raw = f"{form.age}{form.name}{form.about}"
    # Используем только MD5 для скорости, SHA256 убираем если не нужен
    md5 = hashlib.md5(raw.encode()).hexdigest()
    sha256 = hashlib.sha256(raw.encode()).hexdigest()
    return md5, sha256, raw


class MediaDownloader:
    """Класс для параллельной загрузки медиафайлов"""
    
    def __init__(self, max_concurrent_downloads=3):
        self.max_concurrent_downloads = max_concurrent_downloads
        self.semaphore = asyncio.Semaphore(max_concurrent_downloads)
    
    async def download_single_media(self, msg, save_path: str, file_extension: str):
        """Загрузка одного медиафайла с ограничением"""
        async with self.semaphore:
            try:
                filename = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4)) + file_extension
                file_path = os.path.join(save_path, filename)
                await msg.download_media(file_path)
                return filename
            except Exception as e:
                logger.error(f"Ошибка загрузки медиафайла: {e}")
                return None
    
    async def download_all_media(self, photo_msgs: List, video_msgs: List, save_dir: str):
        """Параллельная загрузка всех медиафайлов"""
        tasks = []
        
        # Добавляем задачи для фото
        for msg in photo_msgs:
            tasks.append(self.download_single_media(msg, save_dir, ".jpg"))
        
        # Добавляем задачи для видео
        for msg in video_msgs:
            tasks.append(self.download_single_media(msg, save_dir, ".mp4"))
        
        # Выполняем все загрузки параллельно
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successful_downloads = [r for r in results if r and not isinstance(r, Exception)]
            logger.info(f"Загружено медиафайлов: {len(successful_downloads)}/{len(tasks)}")


# Глобальный экземпляр загрузчика
media_downloader = MediaDownloader()


async def save_form_optimized(worker, form, rnd_id, photo_msgs, video_msgs):
    """Оптимизированное сохранение анкеты с параллельной загрузкой медиа"""
    try:
        save_dir = os.path.join(worker.config["parse"]["save_dir"], rnd_id)
        
        # Создаем директорию асинхронно
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            await loop.run_in_executor(executor, os.makedirs, save_dir, True)
        
        # Подготавливаем данные
        md5, sha256, raw = hash_form_result(form)
        
        # Создаем все данные для записи
        desc_content = f"Имя: {form.name}\nВозраст: {form.age}\nО себе: {form.about}"
        form_data = {
            "raw": raw,
            "name": form.name,
            "age": form.age,
            "about": form.about,
            "md5": md5,
            "sha256": sha256,
            "timestamp": time.time()
        }
        
        # Создаем задачи для параллельного выполнения
        tasks = []
        
        # Задача сохранения текстовых файлов
        async def save_text_files():
            with ThreadPoolExecutor() as executor:
                loop = asyncio.get_event_loop()
                
                # Сохраняем описание
                desc_path = os.path.join(save_dir, "desc.txt")
                await loop.run_in_executor(
                    executor,
                    lambda: open(desc_path, "w", encoding="utf-8").write(desc_content)
                )
                
                # Сохраняем hash для обратной совместимости
                hash_path = os.path.join(save_dir, "hash.txt")
                await loop.run_in_executor(
                    executor,
                    lambda: open(hash_path, "w", encoding="utf-8").write(md5)
                )
                
                # Сохраняем JSON
                json_path = os.path.join(save_dir, "form.json")
                await loop.run_in_executor(
                    executor,
                    lambda: json.dump(form_data, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                )
        
        # Добавляем задачи
        tasks.append(save_text_files())
        
        # Задача загрузки медиафайлов (если есть)
        if photo_msgs or video_msgs:
            tasks.append(media_downloader.download_all_media(photo_msgs, video_msgs, save_dir))
        
        # Выполняем все задачи параллельно
        await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info(f"Анкета сохранена: {rnd_id}")
        
    except Exception as e:
        logger.error(f"Ошибка сохранения анкеты: {e}")


# Функция для обратной совместимости
async def save_form(worker, form, rnd_id, photo_msgs, video_msgs, _):
    """Обертка для обратной совместимости"""
    await save_form_optimized(worker, form, rnd_id, photo_msgs, video_msgs)


class PerformanceMonitor:
    """Улучшенный монитор производительности для отслеживания узких мест"""
    
    def __init__(self):
        self.stats = {
            'form_processing_times': [],
            'api_call_times': [],
            'media_download_times': [],
            'validation_times': [],
            'file_io_times': []
        }
        self.operation_counts = {category: 0 for category in self.stats.keys()}
        self.start_time = time.time()
        
        # Пороги для предупреждений
        self.warning_thresholds = {
            'form_processing_times': 1.0,  # Если обработка формы > 1 сек
            'api_call_times': 2.0,         # Если API вызов > 2 сек
            'media_download_times': 5.0,   # Если загрузка медиа > 5 сек
            'validation_times': 0.1,       # Если валидация > 0.1 сек
            'file_io_times': 0.5           # Если файловая операция > 0.5 сек
        }
    
    def record_time(self, category: str, duration: float, operation_name: str = ""):
        """Записать время выполнения операции с дополнительной информацией"""
        if category in self.stats:
            self.stats[category].append(duration)
            self.operation_counts[category] += 1
            
            # Проверяем пороги предупреждений
            threshold = self.warning_thresholds.get(category, float('inf'))
            if duration > threshold:
                op_info = f" ({operation_name})" if operation_name else ""
                logger.warning(f"Медленная операция {category}{op_info}: {duration:.3f}с")
    
    def get_average_time(self, category: str) -> float:
        """Получить среднее время выполнения"""
        times = self.stats.get(category, [])
        return sum(times) / len(times) if times else 0.0
    
    def get_percentile(self, category: str, percentile: int = 95) -> float:
        """Получить процентиль времени выполнения"""
        times = self.stats.get(category, [])
        if not times:
            return 0.0
        
        sorted_times = sorted(times)
        index = int((percentile / 100) * len(sorted_times))
        return sorted_times[min(index, len(sorted_times) - 1)]
    
    def get_operations_per_second(self, category: str) -> float:
        """Получить количество операций в секунду"""
        count = self.operation_counts.get(category, 0)
        elapsed = time.time() - self.start_time
        return count / elapsed if elapsed > 0 else 0.0
    
    def print_stats(self):
        """Вывести детальную статистику производительности"""
        logger.info("=== СТАТИСТИКА ПРОИЗВОДИТЕЛЬНОСТИ ===")
        
        for category, times in self.stats.items():
            if times:
                avg_time = self.get_average_time(category)
                p95_time = self.get_percentile(category, 95)
                ops_per_sec = self.get_operations_per_second(category)
                count = len(times)
                
                logger.info(f"{category}:")
                logger.info(f"  Операций: {count}")
                logger.info(f"  Среднее время: {avg_time:.3f}с")
                logger.info(f"  95-й процентиль: {p95_time:.3f}с")
                logger.info(f"  Операций/сек: {ops_per_sec:.2f}")
                
                # Рекомендации по оптимизации
                if avg_time > self.warning_thresholds.get(category, float('inf')):
                    self._print_optimization_recommendations(category, avg_time)
        
        total_runtime = time.time() - self.start_time
        logger.info(f"Общее время работы: {total_runtime:.2f}с")
    
    def _print_optimization_recommendations(self, category: str, avg_time: float):
        """Вывести рекомендации по оптимизации"""
        recommendations = {
            'form_processing_times': [
                "Рассмотрите увеличение размера кэша парсера",
                "Проверьте регулярные выражения на эффективность",
                "Возможно, стоит предкомпилировать regex"
            ],
            'api_call_times': [
                "Увеличьте таймауты для API вызовов",
                "Рассмотрите пакетную обработку запросов",
                "Проверьте сетевое соединение"
            ],
            'media_download_times': [
                "Увеличьте количество параллельных загрузок",
                "Проверьте скорость интернет-соединения",
                "Рассмотрите использование CDN или прокси"
            ],
            'validation_times': [
                "Оптимизируйте алгоритмы валидации",
                "Используйте set() вместо list() для поиска",
                "Предкомпилируйте регулярные выражения"
            ],
            'file_io_times': [
                "Рассмотрите использование SSD вместо HDD",
                "Увеличьте размер буфера для файловых операций",
                "Используйте асинхронные файловые операции"
            ]
        }
        
        if category in recommendations:
            logger.warning(f"Рекомендации для {category} (среднее: {avg_time:.3f}с):")
            for rec in recommendations[category]:
                logger.warning(f"  • {rec}")
    
    def reset_stats(self):
        """Сбросить статистику"""
        for category in self.stats:
            self.stats[category].clear()
            self.operation_counts[category] = 0
        self.start_time = time.time()


# Глобальный монитор производительности
performance_monitor = PerformanceMonitor() 