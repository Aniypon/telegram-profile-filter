import asyncio
import sys
import time
from typing import List, Tuple, Optional

import emoji
import os
from loguru import logger
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, Message
from telethon import errors  # Добавляем импорт для обработки ошибок
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()


class TelegramOptimized:
    def __init__(self, worker):
        self.worker = worker
        self.client = worker.client
        self.bot_name = worker.bot_name
        self._message_cache = []
        self._cache_timestamp = 0
        self._cache_duration = 0.5  # Увеличиваем до 0.5 сек для лучшей производительности
        self._last_processed_message_id = 0  # Отслеживаем ID последнего обработанного сообщения
        self._flood_wait_threshold = 60  # Автоматически ждем flood wait до 60 сек
        self._request_count = 0  # Счетчик запросов для адаптивной задержки
        self._last_request_time = 0  # Время последнего запроса
        
    async def check_tg_account(self):
        """Авторизация в Telegram с оптимизацией"""
        logger.info("Проверка аккаунта Telegram...")
        try:
            api_id = os.getenv('TELEGRAM_API_ID')
            api_hash = os.getenv('TELEGRAM_API_HASH')
            
            if not api_id or not api_hash:
                raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть установлены в .env файле")
            
            self.worker.client = TelegramClient('QueVinchik', api_id=int(api_id), api_hash=api_hash)
            self.client = self.worker.client
            await self.client.start()
            
            me = await self.client.get_me()
            logger.success("@" + me.username)
            
            # Отправляем стартовое сообщение без ожидания
            await self.client.send_message(self.bot_name, emoji.emojize(":thumbs_down:"))
            # Настраиваем flood wait threshold
            self.client.flood_sleep_threshold = self._flood_wait_threshold
            logger.info("Аккаунт Telegram подключен")
        except Exception as e:
            logger.exception(f"Ошибка авторизации в Telegram: {e}")
            sys.exit()

    async def _safe_request(self, request_func, *args, **kwargs):
        """Безопасное выполнение запроса с обработкой FloodWaitError и адаптивной задержкой"""
        current_time = time.time()
        
        # Адаптивная задержка между запросами
        time_since_last = current_time - self._last_request_time
        min_delay = self.worker.config.get("telegram", {}).get("min_delay", 0.1)
        
        if time_since_last < min_delay:
            await asyncio.sleep(min_delay - time_since_last)
        
        self._last_request_time = time.time()
        self._request_count += 1
        
        # Экспоненциальная задержка каждые 10 запросов
        if self._request_count % 10 == 0:
            backoff_delay = min(0.1 * (self._request_count // 10), 2.0)
            logger.debug(f"Backoff задержка: {backoff_delay:.2f}сек после {self._request_count} запросов")
            await asyncio.sleep(backoff_delay)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await request_func(*args, **kwargs)
            except errors.FloodWaitError as e:
                if e.seconds <= self._flood_wait_threshold:
                    logger.warning(f"FloodWaitError: ждем {e.seconds}сек (попытка {attempt + 1}/{max_retries})")
                    await asyncio.sleep(e.seconds)
                    continue
                else:
                    logger.error(f"FloodWaitError слишком долгий: {e.seconds}сек, пропускаем")
                    raise
            except errors.SlowModeWaitError as e:
                logger.warning(f"SlowModeWaitError: ждем {e.seconds}сек")
                await asyncio.sleep(e.seconds)
                continue
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # Экспоненциальная задержка: 1, 2, 4 сек
                    logger.warning(f"Ошибка запроса: {e}, повтор через {delay}сек")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
        
        raise Exception(f"Не удалось выполнить запрос после {max_retries} попыток")

    async def _get_cached_messages(self, limit: int = 10) -> List[Message]:
        """Получение сообщений с кэшированием"""
        current_time = time.time()
        
        # Проверяем актуальность кэша
        if (current_time - self._cache_timestamp) < self._cache_duration and self._message_cache:
            return self._message_cache[:limit]
        
        # Обновляем кэш с безопасным запросом
        try:
            self._message_cache = await self._safe_request(
                self.client.get_messages, 
                self.bot_name, 
                limit=limit
            )
            self._cache_timestamp = current_time
            return self._message_cache
        except Exception as e:
            logger.error(f"Ошибка получения сообщений: {e}")
            return []

    async def wait_bot_response(self) -> str:
        """Оптимизированное ожидание ответа от бота"""
        max_attempts = 30  # Уменьшаем количество попыток
        attempt = 0
        
        while attempt < max_attempts:
            messages = await self._get_cached_messages(limit=5)
            
            for msg in messages:
                if msg.text and msg.id > self._last_processed_message_id:
                    try:
                        sender = await self._safe_request(msg.get_sender)
                        if sender.username == self.bot_name:
                            self._last_processed_message_id = msg.id
                            return msg.text
                    except Exception as e:
                        logger.debug(f"Ошибка получения отправителя: {e}")
                        continue
            
            # Более агрессивная адаптивная задержка
            if attempt < 5:
                delay = 0.1  # Первые 5 попыток - быстро
            elif attempt < 15:
                delay = 0.2  # Следующие 10 - умеренно
            else:
                delay = 0.5  # Остальные - медленно
                
            await asyncio.sleep(delay)
            attempt += 1
            
            # Принудительно инвалидируем кэш каждые 5 попыток
            if attempt % 5 == 0:
                self._cache_timestamp = 0
                logger.debug(f"Инвалидация кэша после {attempt} попыток")
                
        raise TimeoutError("Не удалось получить ответ от бота")

    async def get_bot_response_media(self) -> Tuple[List[Message], List[Message]]:
        """Оптимизированное получение медиафайлов"""
        messages = await self._get_cached_messages(limit=5)
        
        photos = []
        videos = []
        
        for msg in messages:
            try:
                sender = await msg.get_sender()
                if sender.username != self.bot_name:
                    break
                    
                if isinstance(msg.media, MessageMediaPhoto):
                    photos.append(msg)
                elif isinstance(msg.media, MessageMediaDocument):
                    videos.append(msg)
            except Exception as e:
                logger.debug(f"Ошибка обработки сообщения: {e}")
                continue
                
        return photos, videos

    async def skip_form(self):
        """Быстрый пропуск анкеты"""
        await self._safe_request(
            self.client.send_message, 
            self.bot_name, 
            emoji.emojize(":thumbs_down:")
        )
        # Инвалидируем кэш чтобы следующий запрос получил свежие данные
        self._cache_timestamp = 0
        self._message_cache = []

    async def send_reply(self, text: str):
        """Оптимизированная отправка ответа"""
        text = text.capitalize()
        logger.info(f"Отправка ответа: {text}")
        await self._safe_request(
            self.client.send_message, 
            self.bot_name, 
            text
        )
        # Инвалидируем кэш после отправки сообщения
        self._cache_timestamp = 0
        self._message_cache = []

    async def wait_user_action(self) -> bool:
        """Оптимизированное ожидание действия пользователя"""
        logger.info("Ожидание действия пользователя (лайк/дизлайк)...")
        logger.info("💡 Совет: Отправьте ❤️ для лайка или 👎 для дизлайка в чате с ботом")
        
        # Получаем текущий ID пользователя один раз
        me = await self.client.get_me()
        my_id = me.id
        
        # Получаем начальные сообщения без кэша для точности
        try:
            initial_messages = await self._safe_request(
                self.client.get_messages, 
                self.bot_name, 
                limit=3
            )
            last_msg_id = initial_messages[0].id if initial_messages else 0
        except Exception as e:
            logger.error(f"Ошибка получения начальных сообщений: {e}")
            last_msg_id = 0
        
        max_wait_time = 180  # Увеличиваем до 3 минут для лучшего UX
        start_time = time.time()
        check_count = 0
        last_status_time = start_time
        
        while (time.time() - start_time) < max_wait_time:
            check_count += 1
            
            # Адаптивная частота проверки
            if check_count < 50:
                delay = 0.2  # Первые 10 секунд - часто
            elif check_count < 150:
                delay = 0.5  # Следующие 50 секунд - умеренно  
            else:
                delay = 1.0  # Остальное время - реже
                
            await asyncio.sleep(delay)
            
            try:
                # Принудительно получаем свежие сообщения с защищенным запросом
                messages = await self._safe_request(
                    self.client.get_messages, 
                    self.bot_name, 
                    limit=5
                )
                
                for msg in messages:
                    # Пропускаем старые сообщения
                    if msg.id <= last_msg_id:
                        continue
                        
                    # Проверяем только сообщения от текущего пользователя
                    if hasattr(msg, 'from_id') and msg.from_id and msg.from_id.user_id == my_id:
                        if msg.text:
                            # Логируем найденное сообщение для отладки
                            logger.debug(f"Найдено сообщение от пользователя: '{msg.text}' (ID: {msg.id})")
                            
                            # Проверяем точные эмодзи как указал пользователь
                            if "❤️" in msg.text:
                                logger.success(f"✅ Обнаружен лайк пользователя: '{msg.text}'")
                                return True
                            elif "👎" in msg.text:
                                logger.info(f"❌ Обнаружен дизлайк пользователя: '{msg.text}'")
                                return False
                            
                            # Обновляем last_msg_id для следующих итераций
                            last_msg_id = max(last_msg_id, msg.id)
                
                # Выводим статус каждые 15 секунд
                current_time = time.time()
                if current_time - last_status_time >= 15:
                    elapsed = current_time - start_time
                    remaining = max_wait_time - elapsed
                    logger.info(f"⏳ Ожидание продолжается... {elapsed:.0f}сек прошло, {remaining:.0f}сек осталось")
                    last_status_time = current_time
                
            except Exception as e:
                logger.error(f"Ошибка при проверке действий пользователя: {e}")
                # Небольшая задержка при ошибке
                await asyncio.sleep(0.5)
                continue
                
        elapsed = time.time() - start_time
        logger.warning(f"⏰ Тайм-аут ожидания действия пользователя ({elapsed:.1f}сек)")
        logger.warning("💡 Попробуйте еще раз отправить ❤️ или 👎 в чате с ботом")
        
        # Автоматически сбрасываем состояние при тайм-ауте
        logger.info("🔄 Автоматический сброс состояния из-за тайм-аута...")
        await self.reset_state()
        
        return False

    async def reset_state(self):
        """Сброс состояния для решения проблем с зависанием"""
        logger.info("🔄 Сброс состояния TelegramOptimized...")
        self._cache_timestamp = 0
        self._message_cache = []
        self._last_processed_message_id = 0
        
        # Отправляем дизлайк чтобы "разблокировать" бота
        try:
            await self.client.send_message(self.bot_name, emoji.emojize(":thumbs_down:"))
            await asyncio.sleep(0.5)  # Даем время боту обработать
            logger.success("✅ Состояние сброшено, отправлен дизлайк для разблокировки")
        except Exception as e:
            logger.error(f"Ошибка при сбросе состояния: {e}")


# Функции для обратной совместимости
async def check_tg_account(worker):
    tg_opt = TelegramOptimized(worker)
    await tg_opt.check_tg_account()

async def wait_bot_response(worker):
    # Используем единый экземпляр если доступен, иначе создаем новый
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        return await worker.telegram_optimized.wait_bot_response()
    else:
        tg_opt = TelegramOptimized(worker)
        return await tg_opt.wait_bot_response()

async def get_bot_response_media(worker):
    # Используем единый экземпляр если доступен, иначе создаем новый
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        return await worker.telegram_optimized.get_bot_response_media()
    else:
        tg_opt = TelegramOptimized(worker)
        return await tg_opt.get_bot_response_media()

async def skip_form(worker):
    # Используем единый экземпляр если доступен, иначе создаем новый
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        await worker.telegram_optimized.skip_form()
    else:
        tg_opt = TelegramOptimized(worker)
        await tg_opt.skip_form()

async def send_reply(worker, text):
    # Используем единый экземпляр если доступен, иначе создаем новый
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        await worker.telegram_optimized.send_reply(text)
    else:
        tg_opt = TelegramOptimized(worker)
        await tg_opt.send_reply(text)

async def wait_user_action(worker):
    # Используем единый экземпляр если доступен, иначе создаем новый
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        return await worker.telegram_optimized.wait_user_action()
    else:
        tg_opt = TelegramOptimized(worker)
        return await tg_opt.wait_user_action()

async def reset_telegram_state(worker):
    """Сброс состояния Telegram для решения проблем с зависанием"""
    if hasattr(worker, 'telegram_optimized') and worker.telegram_optimized:
        await worker.telegram_optimized.reset_state()
    else:
        tg_opt = TelegramOptimized(worker)
        await tg_opt.reset_state() 