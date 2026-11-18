import asyncio
import hashlib
import os
import random
import re
import string
import sys
import time
from collections import deque
from typing import Set

from loguru import logger

from .configs import load_configs
from .parser import parse_form, form_parser, batch_processor
from .telegram import (check_tg_account, get_bot_response_media, send_reply,
                       skip_form, wait_bot_response, wait_user_action, TelegramOptimized, reset_telegram_state)
from .utils import (hash_form_result, notify, performance_monitor, save_form,
                    update_stat)
from .validators import (check_about_banwords, check_about_len, check_age,
                         check_promoting, optimized_validators)


# Form и parse_form теперь импортируются из parser_optimized.py


class DVWorker:
    def __init__(self):
        self.bot_name = "leomatchbot"  # Тег бота
        self.client = None  # Клиент Telegram
        self.pool = None
        self.telegram_optimized = None  # Единый экземпляр TelegramOptimized

        self.skipped_forms = 0  # Количество пропущенных анкет
        self.suitable_forms = 0  # Количество подходящих анкет
        self.all_forms = 0  # Всего обработано анкет

        self.config = { }  # Общий конфиг
        self.banwords = []  # Бан-слова
        self.promotings = []  # Рекламные сообщения

        # Оптимизированный хранилище хешей с ограниченным размером
        self.hash_set: Set[str] = set()
        self.hash_deque = deque(maxlen=10000)  # Ограничиваем размер
        
        # Статистика производительности
        self.processing_times = deque(maxlen=100)  # Последние 100 времен обработки
        self.last_stat_update = 0
        self.stat_update_interval = 2.0  # Обновляем статистику раз в 2 секунды

    async def run(self):
        logger.info("Запуск QueVinchik...")
        await load_configs(self)
        await check_tg_account(self)
        
        # Создаем единый экземпляр TelegramOptimized после подключения к Telegram
        self.telegram_optimized = TelegramOptimized(self)
        logger.info("TelegramOptimized инициализирован")
        
        await self._update_stat_if_needed()
        logger.success("QueVinchik запущен")
        
        # Включаем мониторинг производительности если настроено
        if self.config.get("performance", {}).get("enable_performance_monitoring", False):
            logger.info("Мониторинг производительности включен")
        
        try:
            if self.config["work_mode"] == "search":
                await self.search_cycle()
            elif self.config["work_mode"] == "parse":
                await self.parse_cycle()
            else:
                logger.exception(f"Неправильный режим работы: {self.config['work_mode']}, поддерживаются search и parse")
                sys.exit()
        finally:
            # Выводим статистику производительности при завершении
            if self.config.get("performance", {}).get("enable_performance_monitoring", False):
                performance_monitor.print_stats()
                cache_stats = form_parser.get_cache_stats()
                validator_stats = optimized_validators.get_stats()
                logger.info(f"Статистика кэша парсера: {cache_stats}")
                logger.info(f"Статистика валидаторов: {validator_stats}")

    def _add_hash_optimized(self, md5_hash: str) -> bool:
        """Оптимизированное добавление хеша с проверкой дубликатов"""
        if md5_hash in self.hash_set:
            return False  # Дубликат найден
        
        self.hash_set.add(md5_hash)
        self.hash_deque.append(md5_hash)
        
        # Если deque переполнен, удаляем старый хеш из set
        if len(self.hash_deque) == self.hash_deque.maxlen:
            old_hash = self.hash_deque[0]  # Самый старый
            self.hash_set.discard(old_hash)
        
        return True  # Новый хеш

    async def _update_stat_if_needed(self):
        """Обновление статистики с ограничением частоты"""
        current_time = time.time()
        if current_time - self.last_stat_update >= self.stat_update_interval:
            await update_stat(self)
            self.last_stat_update = current_time

    async def _process_form_validation(self, form, form_string: str) -> tuple[bool, str]:
        """Централизованная валидация формы"""
        # Проверка возраста
        age, good = check_age(self, form.age, form.about)
        if not good:
            return False, f"Скип анкеты - Возраст - {age} | {form_string}"
        
        # Проверка длины описания
        about_len, good = check_about_len(self, form.about)
        if not good:
            return False, f"Скип анкеты - Длина описания - {about_len} символов | {form_string}"
        
        # Проверка банвордов
        banword, good = check_about_banwords(self, form.about)
        if not good:
            return False, f"Скип анкеты - Банворд в описание - {banword} | {form_string}"
        
        return True, ""

    async def parse_cycle(self):
        msg = ""
        batch_counter = 0
        
        while True:
            start_time = time.time()
            
            # Адаптивная задержка для оптимизации производительности
            delay = self.config["telegram"].get("delay_between_requests", 0.2)
            await asyncio.sleep(delay)
            
            # Генерируем уникальный ID более эффективно
            rnd_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
            save_dir = os.path.join(self.config["parse"]["save_dir"], rnd_id)
            counter = 0
            while os.path.exists(save_dir) and counter < 100:
                rnd_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                save_dir = os.path.join(self.config["parse"]["save_dir"], rnd_id)
                counter += 1
            
            try:
                msg = await wait_bot_response(self)
                
                # Проверка рекламы
                is_promo, reply = check_promoting(self, msg)
                if is_promo:
                    logger.warning(f"Скип рекламы - {msg}")
                    await send_reply(self, reply)
                    continue
                
                # Параллельная загрузка медиа и парсинг формы
                media_task = asyncio.create_task(get_bot_response_media(self))
                form = parse_form(msg)
                photo_msgs, video_msgs = await media_task
                
                self.all_forms += 1
                form_string = f"{form.name} | {form.age}{f' | {form.about}' if form.about != '' else ''}"
                
                # Проверка дубликатов (только если есть описание)
                if form.about:
                    md5, sha256, raw = hash_form_result(form)
                    if not self._add_hash_optimized(md5):
                        logger.error(f"Скип анкеты - Дубликат | {form_string}")
                        await skip_form(self)
                        self.skipped_forms += 1
                        continue
                
                # Валидация формы
                is_valid, error_msg = await self._process_form_validation(form, form_string)
                if not is_valid:
                    logger.error(error_msg)
                    await skip_form(self)
                    self.skipped_forms += 1
                    continue
                
                # Создаем финальный ID и сохраняем
                safe_name = re.sub(r'[^\w_. -]', '', form.name)
                rnd_id = f"{safe_name}_{form.age}_{rnd_id}"
                
                # Асинхронное сохранение
                save_task = asyncio.create_task(save_form(self, form, rnd_id, photo_msgs, video_msgs, None))
                await save_task
                logger.success(form_string)
                
                # Периодическое обновление статистики
                batch_counter += 1
                if batch_counter % 5 == 0:  # Каждые 5 анкет
                    await self._update_stat_if_needed()
                
                processing_time = time.time() - start_time
                self.processing_times.append(processing_time)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга анкеты: {msg} - {e}")
            
            await skip_form(self)

    async def search_cycle(self):
        msg = ""
        last_processed_msg = ""  # Добавляем отслеживание последней обработанной анкеты
        repeat_counter = 0  # Счетчик повторов одной анкеты
        max_repeats = 3  # Максимум повторов перед принудительным скипом
        
        while True:
            start_time = time.time()
            
            # Адаптивная задержка для оптимизации производительности
            delay = self.config["telegram"].get("delay_between_requests", 0.2)
            await asyncio.sleep(delay)
            
            try:
                msg = await wait_bot_response(self)
                
                # Проверяем, не получаем ли мы ту же анкету снова
                if msg == last_processed_msg:
                    repeat_counter += 1
                    logger.warning(f"Повторное получение той же анкеты ({repeat_counter}/{max_repeats}): {msg[:50]}...")
                    
                    if repeat_counter >= max_repeats:
                        logger.error(f"Слишком много повторов одной анкеты, принудительный скип")
                        await skip_form(self)
                        # Увеличиваем задержку чтобы дать боту время обновиться
                        await asyncio.sleep(1.0)
                        last_processed_msg = ""
                        repeat_counter = 0
                        continue
                    else:
                        # Небольшая задержка и повторная попытка
                        await asyncio.sleep(0.5)
                        continue
                else:
                    # Новая анкета, сбрасываем счетчик
                    repeat_counter = 0
                    last_processed_msg = msg
                
                # Проверка рекламы
                is_promo, reply = check_promoting(self, msg)
                if is_promo:
                    logger.warning(f"Скип рекламы - {msg}")
                    await send_reply(self, reply)
                    continue
                
                self.all_forms += 1
                form = parse_form(msg)
                form_string = f"{form.name} | {form.age}{f' | {form.about}' if form.about != '' else ''}"
                
                # Валидация формы
                is_valid, error_msg = await self._process_form_validation(form, form_string)
                if not is_valid:
                    logger.error(error_msg)
                    await skip_form(self)
                    self.skipped_forms += 1
                    await self._update_stat_if_needed()
                    continue
                
                # Параллельная загрузка медиа
                media_task = asyncio.create_task(get_bot_response_media(self))
                photo_msgs, video_msgs = await media_task
                
                # Показываем форму и ждем действие пользователя
                await self.show_form(form, photo_msgs, video_msgs)
                
                processing_time = time.time() - start_time
                self.processing_times.append(processing_time)
                
            except Exception as e:
                logger.error(f"Ошибка парсинга анкеты: {msg} - {e}")
                await skip_form(self)
                # Увеличиваем задержку после ошибки
                await asyncio.sleep(0.3)
                # КРИТИЧНО: добавляем continue чтобы начать новую итерацию
                continue

    async def show_form(self, form, photo_msgs=None, video_msgs=None):
        logger.success("")
        logger.success("Подходящая анкета найдена")
        logger.success(f"Имя: {form.name}")
        logger.success(f"Возраст: {form.age}")
        logger.success(f"О себе: {form.about}")
        logger.success("")
        
        # Уведомляем пользователя если включено
        if self.config["search"]["forms"]["notify"]["enabled"]:
            notify(self)
        
        # Если включен мониторинг действий пользователя
        if self.config["search"]["auto_continue"]["monitor_user_actions"]:
            logger.success("Ожидание действия пользователя в Telegram...")
            
            # Ждем действие пользователя в Telegram
            user_liked = await wait_user_action(self)
            
            if user_liked:
                logger.success("Пользователь лайкнул анкету!")
                
                # Сохраняем анкету если разрешено в конфиге
                if self.config["search"]["allow_save"] and photo_msgs is not None and video_msgs is not None:
                    safe_name = re.sub(r'[^\w_. -]', '', form.name)
                    rnd_id = f"{safe_name}_{form.age}_{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5))}"
                    await save_form(self, form, rnd_id, photo_msgs, video_msgs, None)
                    logger.success("Анкета сохранена!")
                
                self.suitable_forms += 1
            else:
                logger.info("Пользователь пропустил анкету")
                self.skipped_forms += 1
            
            await update_stat(self)
            logger.info("Продолжаем поиск...")
        else:
            # Режим без мониторинга - показываем анкету и ждем ручного действия пользователя
            logger.success("Анкета показана. Примите решение в Telegram вручную.")
            logger.info("Нажмите Enter для продолжения после того, как примете решение в Telegram...")
            
            # Ждем ввод от пользователя в консоли
            input()
            
            # Сохраняем анкету если разрешено в конфиге
            if self.config["search"]["allow_save"] and photo_msgs is not None and video_msgs is not None:
                safe_name = re.sub(r'[^\w_. -]', '', form.name)
                rnd_id = f"{safe_name}_{form.age}_{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5))}"
                await save_form(self, form, rnd_id, photo_msgs, video_msgs, None)
                logger.success("Анкета сохранена!")
            
            self.suitable_forms += 1
            await update_stat(self)
            logger.info("Продолжаем поиск...")
            # НЕ вызываем skip_form() - пользователь сам принял решение в Telegram
