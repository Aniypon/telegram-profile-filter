import json
import os
import sys
import time
from functools import lru_cache
from pathlib import Path

from loguru import logger


class ConfigManager:
    """Оптимизированный менеджер конфигураций с кэшированием"""
    
    def __init__(self):
        self._file_cache = {}
        self._file_timestamps = {}
        self._cache_ttl = 30  # TTL кэша в секундах
    
    def _is_cache_valid(self, path: str) -> bool:
        """Проверить актуальность кэша"""
        if path not in self._file_cache:
            return False
        
        try:
            current_mtime = os.path.getmtime(path)
            cached_mtime = self._file_timestamps.get(path, 0)
            cache_time = self._file_cache[path].get('_cache_time', 0)
            
            # Проверяем и время изменения файла, и TTL кэша
            return (current_mtime == cached_mtime and 
                    (time.time() - cache_time) < self._cache_ttl)
        except OSError:
            return False
    
    def _update_cache(self, path: str, data: any):
        """Обновить кэш файла"""
        try:
            self._file_cache[path] = {
                'data': data,
                '_cache_time': time.time()
            }
            self._file_timestamps[path] = os.path.getmtime(path)
        except OSError:
            logger.warning(f"Не удалось получить время изменения файла: {path}")
    
    def read_file_to_list(self, path: str, use_cache: bool = True) -> list:
        """Кэшированное чтение списка из файла"""
        if use_cache and self._is_cache_valid(path):
            logger.debug(f"Использование кэша для {path}")
            return self._file_cache[path]['data']
        
        logger.info(f"Загрузка конфига из {path}")
        try:
            # Используем pathlib для более эффективной работы с файлами
            file_path = Path(path)
            if not file_path.exists():
                logger.warning(f"Файл не найден: {path}")
                return []
            
            # Читаем и обрабатываем файл одним блоком
            with file_path.open('r', encoding='utf-8', errors='ignore') as f:
                lines = f.read().splitlines()
            
            # Фильтруем и нормализуем строки
            processed_lines = [
                line.strip().lower() 
                for line in lines 
                if line.strip() and not line.strip().startswith("#")
            ]
            
            # Удаляем дубликаты, сохраняя порядок
            unique_lines = list(dict.fromkeys(processed_lines))
            
            if use_cache:
                self._update_cache(path, unique_lines)
            
            return unique_lines
            
        except Exception as e:
            logger.error(f"Ошибка загрузки конфига из {path}: {e}")
            return []
    
    def read_json(self, path: str, use_cache: bool = True) -> dict:
        """Кэшированное чтение JSON конфига"""
        if use_cache and self._is_cache_valid(path):
            logger.debug(f"Использование кэша для {path}")
            return self._file_cache[path]['data']
        
        logger.info(f"Загрузка конфига из {path}")
        try:
            file_path = Path(path)
            if not file_path.exists():
                logger.error(f"JSON файл не найден: {path}")
                sys.exit(1)
            
            with file_path.open('r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
            
            if use_cache:
                self._update_cache(path, data)
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON из {path}: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Ошибка загрузки конфига из {path}: {e}")
            sys.exit(1)
    
    def get_cache_stats(self) -> dict:
        """Получить статистику кэша"""
        return {
            'cached_files': len(self._file_cache),
            'cache_files': list(self._file_cache.keys()),
            'cache_size_bytes': sum(
                len(str(data)) for data in self._file_cache.values()
            )
        }
    
    def clear_cache(self):
        """Очистить кэш"""
        self._file_cache.clear()
        self._file_timestamps.clear()


# Глобальный экземпляр менеджера конфигураций
config_manager = ConfigManager()


# Функции для обратной совместимости
def read_file_to_list(path: str) -> list:
    return config_manager.read_file_to_list(path)


def read_json(path: str) -> dict:
    return config_manager.read_json(path)


async def load_configs(worker) -> None:
    """Оптимизированная загрузка всех конфигов"""
    start_time = time.time()
    
    # Загружаем конфиги параллельно (если возможно)
    try:
        worker.config = config_manager.read_json("configs/main_config.json")
        worker.banwords = config_manager.read_file_to_list("configs/banwords.txt")
        worker.promotings = config_manager.read_file_to_list("configs/promotings.txt")
        
        # Обновляем оптимизированные валидаторы
        from .validators import optimized_validators
        optimized_validators.update_banwords(worker.banwords)
        optimized_validators.update_promotings(worker.promotings)
        
        load_time = time.time() - start_time
        logger.info(f"Конфиги загружены за {load_time:.3f}с")
        
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигов: {e}")
        sys.exit(1)
