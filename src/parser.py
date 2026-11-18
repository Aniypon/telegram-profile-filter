import re
import time
from functools import lru_cache
from typing import Optional

from loguru import logger


class Form:
    """Оптимизированный класс анкеты"""
    __slots__ = ['name', 'age', 'about', '_hash']
    
    def __init__(self, name: str, age: str, about: str):
        self.name = name
        self.age = age
        self.about = about
        self._hash = None
    
    def __hash__(self):
        if self._hash is None:
            self._hash = hash((self.name, self.age, self.about))
        return self._hash
    
    def __eq__(self, other):
        if not isinstance(other, Form):
            return False
        return (self.name, self.age, self.about) == (other.name, other.age, other.about)


class OptimizedFormParser:
    """Оптимизированный парсер анкет с кэшированием и скомпилированными регулярными выражениями"""
    
    def __init__(self):
        # Предкомпилированные регулярные выражения для лучшей производительности
        self.name_pattern = re.compile(r"([\s\S]+),\s\d{2},")
        self.age_pattern = re.compile(r"[\s\S]+,\s(\d{1,2}),")
        self.about_pattern = re.compile(r"[\s\S]+?,\s\d{1,2},\s[\s\S]+?\s–\s([\s\S]+)")
        
        # Кэш для уже обработанных форм
        self._parse_cache = {}
        self._cache_hits = 0
        self._cache_misses = 0
    
    @lru_cache(maxsize=1000)
    def _clean_text(self, text: str) -> str:
        """Очистка и нормализация текста с кэшированием"""
        return text.strip().replace("\n", ". ").capitalize()
    
    def parse_form(self, text: str) -> Form:
        """
        Оптимизированный парсинг анкеты с кэшированием результатов
        """
        # Проверяем кэш
        text_hash = hash(text)
        if text_hash in self._parse_cache:
            self._cache_hits += 1
            return self._parse_cache[text_hash]
        
        self._cache_misses += 1
        start_time = time.time()
        
        try:
            # Используем предкомпилированные регулярные выражения
            name_match = self.name_pattern.search(text)
            age_match = self.age_pattern.search(text)
            about_match = self.about_pattern.search(text)
            
            # Обработка имени
            name = name_match.group(1).strip() if name_match else ""
            
            # Обработка возраста
            age = age_match.group(1).strip() if age_match else "0"
            if not age.isdigit():
                age = "0"
            
            # Обработка описания
            if about_match:
                about = self._clean_text(about_match.group(1))
            else:
                about = ""
            
            form = Form(name=name, age=age, about=about)
            
            # Кэшируем результат (ограничиваем размер кэша)
            if len(self._parse_cache) < 500:
                self._parse_cache[text_hash] = form
            
            parse_time = time.time() - start_time
            logger.debug(f"Парсинг анкеты: {parse_time:.3f}с")
            
            return form
            
        except Exception as e:
            logger.error(f"Ошибка парсинга анкеты: {e}")
            return Form(name="", age="0", about="")
    
    def get_cache_stats(self) -> dict:
        """Получить статистику кэша"""
        total_requests = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'cache_hits': self._cache_hits,
            'cache_misses': self._cache_misses,
            'hit_rate': hit_rate,
            'cache_size': len(self._parse_cache)
        }
    
    def clear_cache(self):
        """Очистить кэш"""
        self._parse_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0


# Глобальный экземпляр парсера
form_parser = OptimizedFormParser()


def parse_form(text: str) -> Form:
    """Функция для обратной совместимости"""
    return form_parser.parse_form(text)


class FormBatchProcessor:
    """Класс для батчевой обработки анкет"""
    
    def __init__(self, batch_size: int = 5):
        self.batch_size = batch_size
        self.pending_forms = []
    
    def add_form(self, text: str) -> Optional[list]:
        """Добавить анкету в батч. Возвращает батч если он готов к обработке"""
        self.pending_forms.append(text)
        
        if len(self.pending_forms) >= self.batch_size:
            batch = self.pending_forms.copy()
            self.pending_forms.clear()
            return batch
        
        return None
    
    def process_batch(self, form_texts: list) -> list:
        """Обработать батч анкет"""
        forms = []
        start_time = time.time()
        
        for text in form_texts:
            form = form_parser.parse_form(text)
            forms.append(form)
        
        process_time = time.time() - start_time
        logger.info(f"Обработано батч из {len(forms)} анкет за {process_time:.3f}с")
        
        return forms
    
    def flush(self) -> list:
        """Обработать оставшиеся анкеты в батче"""
        if self.pending_forms:
            batch = self.pending_forms.copy()
            self.pending_forms.clear()
            return self.process_batch(batch)
        return []


# Глобальный экземпляр батч-процессора
batch_processor = FormBatchProcessor() 