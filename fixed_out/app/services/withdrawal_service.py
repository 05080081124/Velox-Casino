"""
app/services/withdrawal_service.py — WithdrawalService для онлайн-казино.

Поддерживаемые провайдеры:
  - PayPal     (вывод + пополнение через REST Payouts API)
  - Google Pay (через платёжный шлюз, заглушка)
  - Apple Pay  (через платёжный шлюз, заглушка)
  - BankTransfer (SWIFT/SEPA банковский перевод, заглушка)
  - LiqPay    (украинский провайдер — депозит + вывод)

Все ключи/токены хранятся в config/payments.json.

Жизненный цикл транзакции:
  pending → processing → completed | failed

Использование:
  from app.services.withdrawal_service import WithdrawalService

  result = WithdrawalService.withdraw(
      user_id=42,
      amount=500.0,
      currency='UAH',
      provider='liqpay',
      meta={'card': '4111111111111111'}
  )
  # result = {'ok': True, 'provider_tx_id': '...', 'balance': 4500.0}

  result = WithdrawalService.deposit(
      user_id=42,
      amount=200.0,
      currency='UAH',
      provider='liqpay',
      meta={'card': '4111111111111111'}
  )
"""
from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.db import get_db
from app.services.wallet_service import WalletService, InsufficientFundsError, WalletError

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config/payments.json')


# ══════════════════════════════════════════════════════════════════════════════
#  Конфигурация
# ══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _provider_cfg(provider_id: str) -> dict:
    """Возвращает конфиг конкретного провайдера из payments.json."""
    cfg = _load_config()
    for s in cfg.get('systems', []):
        if s.get('id') == provider_id:
            return s
    return {}


# ══════════════════════════════════════════════════════════════════════════════
#  Исключения
# ══════════════════════════════════════════════════════════════════════════════

class WithdrawalError(Exception):
    """Базовая ошибка вывода средств."""

class ProviderError(WithdrawalError):
    """Ошибка на стороне платёжного провайдера."""

class WithdrawalLimitError(WithdrawalError):
    """Превышен лимит вывода."""

class CurrencyNotSupportedError(WithdrawalError):
    """Валюта не поддерживается провайдером."""


# ══════════════════════════════════════════════════════════════════════════════
#  База — абстрактный провайдер
# ══════════════════════════════════════════════════════════════════════════════

class BasePaymentProvider(ABC):
    """Базовый класс для всех платёжных провайдеров."""

    provider_id: str = ''
    supported_currencies: list[str] = []

    def _check_currency(self, currency: str):
        if self.supported_currencies and currency not in self.supported_currencies:
            raise CurrencyNotSupportedError(
                f'{self.provider_id}: валюта {currency} не поддерживается. '
                f'Доступно: {", ".join(self.supported_currencies)}'
            )

    @abstractmethod
    def withdraw(
        self,
        user_id: int,
        amount: float,
        currency: str,
        meta: dict,
    ) -> dict:
        """
        Инициировать вывод средств через провайдера.

        Args:
            user_id:  ID пользователя в системе
            amount:   Сумма вывода (без комиссии)
            currency: Код валюты ('UAH', 'USD', 'EUR', ...)
            meta:     Провайдер-специфичные параметры (реквизиты, email и т.д.)

        Returns:
            {'provider_tx_id': str, 'status': 'pending'|'completed', 'fee': float}
        """
        ...

    @abstractmethod
    def deposit(
        self,
        user_id: int,
        amount: float,
        currency: str,
        meta: dict,
    ) -> dict:
        """
        Инициировать пополнение счёта через провайдера.

        Returns:
            {'provider_tx_id': str, 'status': 'pending'|'completed',
             'payment_url': str|None, 'fee': float}
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
#  PayPal
# ══════════════════════════════════════════════════════════════════════════════

class PayPalProvider(BasePaymentProvider):
    """
    PayPal REST API.
    Документация: https://developer.paypal.com/docs/payouts/

    Конфиг (payments.json → id=paypal):
      client_id, client_secret,
      endpoint_staging, endpoint_production
    """

    provider_id = 'paypal'
    supported_currencies = ['USD', 'EUR', 'GBP']

    def __init__(self):
        cfg = _provider_cfg('paypal')
        self._client_id     = cfg.get('client_id', '')
        self._client_secret = cfg.get('client_secret', '')
        test_mode           = _load_config().get('global', {}).get('test_mode', True)
        self._endpoint      = (
            cfg.get('endpoint_staging',    'https://api-m.sandbox.paypal.com/')
            if test_mode else
            cfg.get('endpoint_production', 'https://api-m.paypal.com/')
        )
        self._test_mode = test_mode

    def _get_access_token(self) -> str:
        """Получить OAuth2 токен от PayPal."""
        if self._test_mode or not self._client_id:
            return 'mock_access_token'
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            r = requests.post(
                f'{self._endpoint}v1/oauth2/token',
                auth=HTTPBasicAuth(self._client_id, self._client_secret),
                data={'grant_type': 'client_credentials'},
                timeout=10,
            )
            r.raise_for_status()
            return r.json()['access_token']
        except Exception as e:
            raise ProviderError(f'PayPal auth error: {e}')

    def withdraw(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Вывод через PayPal Payouts API.
        meta: {'paypal_email': 'user@example.com'}
        """
        self._check_currency(currency)
        paypal_email = meta.get('paypal_email', '')
        if not paypal_email:
            raise WithdrawalError('PayPal: не указан email получателя')

        if self._test_mode or not self._client_id:
            # STUB — имитируем успешный вывод
            logger.info(f'[PayPal STUB] withdraw user={user_id} amount={amount} {currency} → {paypal_email}')
            return {
                'provider_tx_id': f'PAYPAL-STUB-{user_id}-{int(time.time())}',
                'status': 'pending',
                'fee': 0.0,
            }

        try:
            import requests
            token = self._get_access_token()
            batch_id = f'velox_{user_id}_{int(time.time())}'
            payload = {
                'sender_batch_header': {
                    'sender_batch_id': batch_id,
                    'email_subject': 'Выплата VELOX Casino',
                },
                'items': [{
                    'recipient_type': 'EMAIL',
                    'amount': {'value': f'{amount:.2f}', 'currency': currency},
                    'receiver': paypal_email,
                    'sender_item_id': batch_id,
                    'note': 'Вывод средств из VELOX Casino',
                }],
            }
            r = requests.post(
                f'{self._endpoint}v1/payments/payouts',
                json=payload,
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get('batch_header', {})
            return {
                'provider_tx_id': batch.get('payout_batch_id', batch_id),
                'status': 'pending',
                'fee': 0.0,
            }
        except Exception as e:
            raise ProviderError(f'PayPal withdraw error: {e}')

    def deposit(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Создать PayPal Order для пополнения баланса.
        Возвращает payment_url для перенаправления пользователя.
        """
        self._check_currency(currency)

        if self._test_mode or not self._client_id:
            logger.info(f'[PayPal STUB] deposit user={user_id} amount={amount} {currency}')
            return {
                'provider_tx_id': f'PAYPAL-DEP-STUB-{user_id}-{int(time.time())}',
                'status': 'pending',
                'payment_url': None,
                'fee': 0.0,
            }

        try:
            import requests
            token = self._get_access_token()
            r = requests.post(
                f'{self._endpoint}v2/checkout/orders',
                json={
                    'intent': 'CAPTURE',
                    'purchase_units': [{
                        'amount': {'currency_code': currency, 'value': f'{amount:.2f}'},
                        'description': 'Пополнение VELOX Casino',
                    }],
                    'application_context': {
                        'return_url': 'https://yourdomain.com/api/payments/callback/paypal',
                        'cancel_url': 'https://yourdomain.com/payments',
                    },
                },
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            approve_url = next(
                (l['href'] for l in data.get('links', []) if l['rel'] == 'approve'), None
            )
            return {
                'provider_tx_id': data['id'],
                'status': 'pending',
                'payment_url': approve_url,
                'fee': 0.0,
            }
        except Exception as e:
            raise ProviderError(f'PayPal deposit error: {e}')


# ══════════════════════════════════════════════════════════════════════════════
#  Google Pay
# ══════════════════════════════════════════════════════════════════════════════

class GooglePayProvider(BasePaymentProvider):
    """
    Google Pay через платёжный шлюз (Stripe, Fondy, WayForPay и др.).
    Google Pay сам по себе — это метод оплаты поверх существующего шлюза.

    Конфиг (payments.json → id=googlepay):
      gateway, gateway_merchant_id, merchant_id, merchant_name,
      endpoint_staging, endpoint_production

    STUB: реализация заглушки. Для реальной работы нужно:
    1. Подключить платёжный шлюз (Stripe/Fondy)
    2. Получить merchant_id в Google Pay Business Console
    3. Реализовать Google Pay JS API на фронтенде
    """

    provider_id = 'googlepay'
    supported_currencies = ['UAH', 'USD', 'EUR', 'RUB']

    def __init__(self):
        cfg = _provider_cfg('googlepay')
        self._gateway             = cfg.get('gateway', 'stripe')
        self._gateway_merchant_id = cfg.get('gateway_merchant_id', '')
        self._merchant_id         = cfg.get('merchant_id', '')
        self._merchant_name       = cfg.get('merchant_name', 'VELOX Casino')
        self._test_mode           = _load_config().get('global', {}).get('test_mode', True)

    def withdraw(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """Google Pay не поддерживает прямой вывод — только пополнение."""
        raise WithdrawalError('Google Pay поддерживает только пополнение. Для вывода используйте банковский перевод.')

    def deposit(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Создать платёжный токен для Google Pay.
        meta: {'payment_token': '<encrypted_google_pay_token>'}

        STUB: Возвращает mock-ответ.
        Для реализации: передайте payment_token в шлюз (Stripe/Fondy).
        """
        self._check_currency(currency)
        payment_token = meta.get('payment_token', '')

        if self._test_mode or not self._gateway_merchant_id:
            logger.info(f'[GooglePay STUB] deposit user={user_id} amount={amount} {currency}')
            return {
                'provider_tx_id': f'GPAY-STUB-{user_id}-{int(time.time())}',
                'status': 'pending',
                'payment_url': None,
                'fee': 0.0,
            }

        # TODO: Передать payment_token в шлюз (пример для Stripe):
        # import stripe
        # stripe.api_key = STRIPE_SECRET_KEY
        # charge = stripe.PaymentIntent.create(
        #     amount=int(amount * 100),
        #     currency=currency.lower(),
        #     payment_method_data={'type': 'card', 'card': {'token': payment_token}},
        #     confirm=True,
        # )
        raise ProviderError('Google Pay: заполните gateway_merchant_id в payments.json')


# ══════════════════════════════════════════════════════════════════════════════
#  Apple Pay
# ══════════════════════════════════════════════════════════════════════════════

class ApplePayProvider(BasePaymentProvider):
    """
    Apple Pay через платёжный шлюз.
    Требования:
    - HTTPS домен с Apple Pay Domain Verification
    - Сертификат Merchant Identity Certificate
    - Шлюз поддерживающий Apple Pay (Stripe, Fondy, WayForPay)

    Конфиг (payments.json → id=applepay):
      merchant_id, merchant_name,
      gateway, gateway_merchant_id,
      domain_verification_file

    STUB: реализация заглушки.
    """

    provider_id = 'applepay'
    supported_currencies = ['UAH', 'USD', 'EUR', 'GBP']

    def __init__(self):
        cfg = _provider_cfg('applepay')
        self._merchant_id         = cfg.get('merchant_id', '')
        self._merchant_name       = cfg.get('merchant_name', 'VELOX Casino')
        self._gateway             = cfg.get('gateway', 'stripe')
        self._gateway_merchant_id = cfg.get('gateway_merchant_id', '')
        self._test_mode           = _load_config().get('global', {}).get('test_mode', True)

    def withdraw(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """Apple Pay не поддерживает прямой вывод."""
        raise WithdrawalError('Apple Pay поддерживает только пополнение. Для вывода используйте банковский перевод.')

    def deposit(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Обработать Apple Pay Payment Token.
        meta: {'payment_token': '<apple_pay_token_dict>'}

        STUB: Возвращает mock-ответ.
        """
        self._check_currency(currency)

        if self._test_mode or not self._merchant_id:
            logger.info(f'[ApplePay STUB] deposit user={user_id} amount={amount} {currency}')
            return {
                'provider_tx_id': f'APAY-STUB-{user_id}-{int(time.time())}',
                'status': 'pending',
                'payment_url': None,
                'fee': 0.0,
            }

        # TODO: Реализовать обработку Apple Pay токена через шлюз.
        # Шаги:
        # 1. Получить PaymentSession от Apple: POST https://apple-pay-gateway.apple.com/paymentservices/startSession
        # 2. Передать payment_token в шлюз (Stripe/Fondy)
        raise ProviderError('Apple Pay: заполните merchant_id и gateway_merchant_id в payments.json')


# ══════════════════════════════════════════════════════════════════════════════
#  Bank Transfer
# ══════════════════════════════════════════════════════════════════════════════

class BankTransferProvider(BasePaymentProvider):
    """
    Банковский перевод (SWIFT / SEPA / локальный).

    Конфиг (payments.json → id=bank_transfer):
      bank_name, account_number, iban, swift_bic,
      beneficiary_name, correspondent_bank,
      min_amount, max_amount, processing_days

    STUB: создаёт ордер для ручной обработки.
    Для автоматизации нужна интеграция с банковским API.
    """

    provider_id = 'bank_transfer'
    supported_currencies = ['UAH', 'USD', 'EUR', 'GBP']

    def __init__(self):
        cfg = _provider_cfg('bank_transfer')
        self._bank_name        = cfg.get('bank_name', 'VELOX Bank')
        self._iban             = cfg.get('iban', '')
        self._swift_bic        = cfg.get('swift_bic', '')
        self._beneficiary      = cfg.get('beneficiary_name', 'VELOX Casino Ltd')
        self._min_amount       = float(cfg.get('min_amount', 500))
        self._max_amount       = float(cfg.get('max_amount', 500000))
        self._processing_days  = int(cfg.get('processing_days', 3))
        self._test_mode        = _load_config().get('global', {}).get('test_mode', True)

    def withdraw(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Банковский перевод на счёт игрока.
        meta: {
            'iban': 'UA...',          # IBAN получателя
            'account_holder': '...',  # Имя владельца счёта
            'bank_name': '...',       # Название банка (опционально)
            'swift': '...',           # SWIFT/BIC (для международных)
        }
        """
        self._check_currency(currency)
        if amount < self._min_amount:
            raise WithdrawalLimitError(f'Банковский перевод: минимум {self._min_amount} {currency}')
        if amount > self._max_amount:
            raise WithdrawalLimitError(f'Банковский перевод: максимум {self._max_amount} {currency}')

        iban = meta.get('iban', '') or meta.get('account_number', '')
        if not iban:
            raise WithdrawalError('Банковский перевод: укажите IBAN или номер счёта')

        tx_id = f'BANK-{user_id}-{int(time.time())}'
        logger.info(
            f'[BankTransfer] withdraw user={user_id} amount={amount} {currency} '
            f'→ IBAN={iban} (обработка {self._processing_days} дн.)'
        )

        # STUB: сохраняем ордер для ручной обработки
        return {
            'provider_tx_id': tx_id,
            'status': 'pending',
            'fee': 0.0,
            'note': f'Перевод будет обработан в течение {self._processing_days} рабочих дней',
        }

    def deposit(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Реквизиты для пополнения банковским переводом.
        Возвращает реквизиты казино для ручного перевода.
        """
        self._check_currency(currency)
        ref = f'DEP-{user_id}-{int(time.time())}'

        return {
            'provider_tx_id': ref,
            'status': 'pending',
            'payment_url': None,
            'fee': 0.0,
            'instructions': {
                'bank_name'       : self._bank_name,
                'iban'            : self._iban,
                'swift_bic'       : self._swift_bic,
                'beneficiary'     : self._beneficiary,
                'reference'       : ref,
                'amount'          : f'{amount:.2f} {currency}',
                'note'            : f'Укажите референс {ref} в назначении платежа',
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
#  LiqPay (Україна)
# ══════════════════════════════════════════════════════════════════════════════

class LiqPayProvider(BasePaymentProvider):
    """
    LiqPay — украинский платёжный провайдер от ПриватБанку.
    Документація: https://www.liqpay.ua/documentation/

    Ключи можно указать двумя способами (приоритет: .env > payments.json):
      .env:           LIQPAY_PUBLIC_KEY=...  LIQPAY_PRIVATE_KEY=...
      payments.json:  systems[id=liqpay] → public_key / private_key
    """

    provider_id = 'liqpay'
    supported_currencies = ['UAH', 'USD', 'EUR']

    def __init__(self):
        cfg = _provider_cfg('liqpay')

        # Ключи: сначала из .env, потом из payments.json
        self._public_key  = (
            os.environ.get('LIQPAY_PUBLIC_KEY')
            or cfg.get('public_key', '')
        ).strip()
        self._private_key = (
            os.environ.get('LIQPAY_PRIVATE_KEY')
            or cfg.get('private_key', '')
        ).strip()

        self._endpoint  = cfg.get('endpoint', 'https://www.liqpay.ua/api/')

        # test_mode: если оба ключа заполнены — продакшн-режим,
        # иначе смотрим global.test_mode в payments.json
        global_test = _load_config().get('global', {}).get('test_mode', True)
        self._test_mode = global_test if (self._public_key and self._private_key) else True

        # BASE_URL для callback/result
        base = os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')
        self._callback_url = (
            os.environ.get('LIQPAY_CALLBACK_URL')
            or cfg.get('callback_url', '').replace('https://yourdomain.com', base)
            or f'{base}/api/payments/callback/liqpay'
        )
        self._result_url = (
            os.environ.get('LIQPAY_RESULT_URL')
            or cfg.get('result_url', '')
            or f'{base}/api/payments/liqpay/return'
        )

    def _sign(self, data_b64: str) -> str:
        """SHA1(private_key + data + private_key) → base64."""
        import base64, hashlib
        raw = self._private_key + data_b64 + self._private_key
        return base64.b64encode(hashlib.sha1(raw.encode('utf-8')).digest()).decode()

    def _build_data(self, params: dict) -> tuple[str, str]:
        """Кодує параметри у base64 і будує підпис."""
        import base64
        data_b64 = base64.b64encode(json.dumps(params, ensure_ascii=False).encode()).decode()
        signature = self._sign(data_b64)
        return data_b64, signature

    def withdraw(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Вивід на картку через LiqPay Payout API.
        meta: {'card': '4111111111111111'}  # картка отримувача
        """
        self._check_currency(currency)
        card = meta.get('card', '') or meta.get('receiver_card', '')
        if not card:
            raise WithdrawalError('LiqPay: вкажіть номер картки для виводу')

        order_id = f'liqpay_out_{user_id}_{int(time.time())}'

        if self._test_mode or not self._public_key:
            logger.info(f'[LiqPay STUB] withdraw user={user_id} amount={amount} {currency} → card={card[-4:]}')
            return {
                'provider_tx_id': f'LIQPAY-STUB-OUT-{user_id}-{int(time.time())}',
                'status': 'pending',
                'fee': 0.0,
            }

        params = {
            'public_key'    : self._public_key,
            'version'       : '3',
            'action'        : 'p2pcredit',
            'amount'        : str(round(amount, 2)),
            'currency'      : currency,
            'description'   : f'Вивід кошт. VELOX Casino #{user_id}',
            'order_id'      : order_id,
            'receiver_card' : card,
            'server_url'    : self._callback_url,
        }
        data_b64, sig = self._build_data(params)

        try:
            import requests
            r = requests.post(
                f'{self._endpoint}3/checkout',
                data={'data': data_b64, 'signature': sig},
                timeout=15,
            )
            r.raise_for_status()
            resp = r.json()
            if resp.get('result') == 'error':
                raise ProviderError(f"LiqPay payout error: {resp.get('err_description', resp)}")
            return {
                'provider_tx_id': resp.get('payment_id', order_id),
                'status': resp.get('status', 'pending'),
                'fee': float(resp.get('commission_credit', 0)),
            }
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f'LiqPay withdraw error: {e}')

    def deposit(self, user_id: int, amount: float, currency: str, meta: dict) -> dict:
        """
        Створити форму оплати LiqPay.
        Повертає data + signature для JS-форми або payment_url.
        meta: {'result_url': 'https://...'}  # опціонально
        """
        self._check_currency(currency)
        order_id = f'liqpay_in_{user_id}_{int(time.time())}'

        if self._test_mode or not self._public_key:
            logger.info(f'[LiqPay STUB] deposit user={user_id} amount={amount} {currency}')
            return {
                'provider_tx_id': f'LIQPAY-STUB-IN-{user_id}-{int(time.time())}',
                'status': 'pending',
                'payment_url': None,
                'fee': 0.0,
            }

        params = {
            'public_key' : self._public_key,
            'version'    : '3',
            'action'     : 'pay',
            'amount'     : str(round(amount, 2)),
            'currency'   : currency,
            'description': f'Поповнення VELOX Casino #{user_id}',
            'order_id'   : order_id,
            'server_url' : self._callback_url,
            'result_url' : meta.get('result_url', self._result_url),
            'sandbox'    : 1 if self._test_mode else 0,
        }
        data_b64, sig = self._build_data(params)

        checkout_url = (
            f'https://www.liqpay.ua/api/3/checkout'
            f'?data={data_b64}&signature={sig}'
        )
        return {
            'provider_tx_id': order_id,
            'status': 'pending',
            'payment_url': checkout_url,
            'liqpay_data': data_b64,
            'liqpay_signature': sig,
            'fee': 0.0,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Реестр провайдеров
# ══════════════════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, type[BasePaymentProvider]] = {
    'paypal'       : PayPalProvider,
    'googlepay'    : GooglePayProvider,
    'applepay'     : ApplePayProvider,
    'bank_transfer': BankTransferProvider,
    'liqpay'       : LiqPayProvider,
}


def get_provider(provider_id: str) -> BasePaymentProvider:
    """Создать экземпляр провайдера по ID."""
    cls = _REGISTRY.get(provider_id.lower())
    if not cls:
        raise WithdrawalError(
            f'Неизвестный провайдер: {provider_id}. '
            f'Доступны: {", ".join(_REGISTRY.keys())}'
        )
    return cls()


# ══════════════════════════════════════════════════════════════════════════════
#  WithdrawalService — главный сервис
# ══════════════════════════════════════════════════════════════════════════════

class WithdrawalService:
    """
    Единая точка входа для операций пополнения и вывода.

    Алгоритм withdraw():
      1. Проверить баланс игрока
      2. Заблокировать сумму (списать с баланса)
      3. Создать транзакцию в БД со статусом 'processing'
      4. Вызвать provider.withdraw(...)
      5. Обновить статус транзакции: 'completed' или 'failed'
      6. При ошибке провайдера — вернуть деньги на баланс

    Алгоритм deposit():
      1. Создать транзакцию 'pending'
      2. Вызвать provider.deposit(...)
      3. Вернуть payment_url (пользователь платит там)
      4. Зачислить средства после подтверждения через webhook
    """

    # Лимиты по умолчанию (переопределяются конфигом провайдера)
    DEFAULT_MIN_WITHDRAW = 10.0
    DEFAULT_MAX_WITHDRAW = 500_000.0
    DEFAULT_MIN_DEPOSIT  = 10.0

    @staticmethod
    def _get_limits(cfg: dict, currency: str) -> tuple[float, float]:
        """Читает лимиты из конфига с учётом валюты."""
        glb = cfg.get('global', {})
        min_key = f'min_deposit_{currency.lower()}'
        min_dep = float(glb.get(min_key, glb.get('min_deposit_uah', WithdrawalService.DEFAULT_MIN_DEPOSIT)))
        return min_dep, WithdrawalService.DEFAULT_MAX_WITHDRAW

    @staticmethod
    def _create_tx(
        db,
        user_id: int,
        tx_type: str,     # 'withdrawal' | 'deposit'
        amount: float,
        currency: str,
        provider: str,
        status: str,
        meta: dict,
    ) -> int:
        """Создать запись транзакции, вернуть её ID."""
        # order_id нужен для confirm_deposit — LiqPay вернёт его в callback
        meta_full = {
            'provider'  : provider,
            'currency'  : currency,
            'order_id'  : meta.get('order_id', ''),   # заполнится позже через _update_order_id
            **meta,
        }
        db.execute(
            """INSERT INTO transactions
               (user_id, type, amount, status, meta, created_at)
               VALUES (%s, %s, %s, %s, %s, NOW())""",
            (user_id, tx_type, amount, status, json.dumps(meta_full)),
        )
        # Поддержка SQLite (lastrowid) и PostgreSQL (RETURNING)
        row = db.fetchone()
        if row:
            return row.get('id', 0)
        return getattr(db, 'lastrowid', 0) or 0

    @staticmethod
    def _update_tx(db, tx_id: int, status: str, provider_tx_id: str = '', extra: dict = None):
        """Обновить статус транзакции и дописать поля в meta (SQLite + PostgreSQL совместимо)."""
        if not tx_id:
            return
        from app.db import DB_ENGINE
        new_fields = {'provider_tx_id': provider_tx_id, 'order_id': provider_tx_id, **(extra or {})}
        if DB_ENGINE == 'postgresql':
            db.execute(
                """UPDATE transactions
                   SET status = %s, meta = meta || %s::jsonb
                   WHERE id = %s""",
                (status, json.dumps(new_fields), tx_id),
            )
        else:
            # SQLite: читаем meta, мержим, пишем обратно
            db.execute('SELECT meta FROM transactions WHERE id = %s', (tx_id,))
            row = db.fetchone()
            if row:
                try:
                    existing = json.loads(row['meta'] or '{}')
                except Exception:
                    existing = {}
                existing.update({'status': status, **new_fields})
                db.execute(
                    'UPDATE transactions SET status = %s, meta = %s WHERE id = %s',
                    (status, json.dumps(existing), tx_id),
                )

    # ── ВЫВОД ─────────────────────────────────────────────────────────────────

    @staticmethod
    def withdraw(
        user_id: int,
        amount: float,
        currency: str,
        provider: str,
        meta: Optional[dict] = None,
    ) -> dict:
        """
        Вывести средства с баланса игрока через указанного провайдера.

        Args:
            user_id:  ID пользователя
            amount:   Сумма вывода
            currency: Код валюты ('UAH', 'USD', ...)
            provider: ID провайдера ('liqpay', 'paypal', 'bank_transfer', ...)
            meta:     Реквизиты получателя (зависит от провайдера)

        Returns:
            {'ok': True, 'provider_tx_id': str, 'balance': float,
             'status': str, 'fee': float}

        Raises:
            InsufficientFundsError, WithdrawalError, ProviderError
        """
        meta = meta or {}
        cfg  = _load_config()

        # Валидация суммы
        if amount <= 0:
            raise WithdrawalError('Сумма вывода должна быть положительной')

        min_w, max_w = WithdrawalService._get_limits(cfg, currency)
        if amount < min_w:
            raise WithdrawalLimitError(f'Минимальная сумма вывода: {min_w} {currency}')
        if amount > max_w:
            raise WithdrawalLimitError(f'Максимальная сумма вывода: {max_w} {currency}')

        # Загружаем провайдер и проверяем валюту
        prov = get_provider(provider)
        prov._check_currency(currency)

        tx_id       = 0
        new_balance = 0.0

        with get_db() as db:
            # 1. Проверяем и списываем баланс
            try:
                new_balance = WalletService.withdraw(
                    user_id, amount,
                    meta={'provider': provider, 'currency': currency, 'status': 'processing'}
                )
            except InsufficientFundsError:
                raise
            except WalletError as e:
                raise WithdrawalError(str(e))

            # 2. Создаём транзакцию со статусом 'processing'
            try:
                tx_id = WithdrawalService._create_tx(
                    db, user_id, 'withdrawal', amount, currency, provider,
                    'processing', meta
                )
            except Exception:
                pass  # транзакция для аудита, не критично если не записалась

        # 3. Вызываем API провайдера
        try:
            result = prov.withdraw(user_id, amount, currency, meta)
        except (WithdrawalError, ProviderError) as e:
            # Возвращаем деньги при ошибке провайдера
            try:
                new_balance = WalletService.cancel_bet(user_id, amount, 'withdrawal_rollback')
            except Exception:
                logger.error(f'[WithdrawalService] CRITICAL: не удалось вернуть {amount} {currency} user={user_id}')

            # Обновляем статус транзакции
            if tx_id:
                with get_db() as db:
                    WithdrawalService._update_tx(db, tx_id, 'failed', extra={'error': str(e)})

            raise

        # 4. Обновляем транзакцию — провайдер подтвердил
        if tx_id:
            with get_db() as db:
                WithdrawalService._update_tx(
                    db, tx_id,
                    result.get('status', 'pending'),
                    result.get('provider_tx_id', ''),
                    extra={'fee': result.get('fee', 0)}
                )

        logger.info(
            f'[WithdrawalService] withdraw OK user={user_id} amount={amount} {currency} '
            f'provider={provider} tx_id={result.get("provider_tx_id")}'
        )

        return {
            'ok'             : True,
            'provider_tx_id' : result.get('provider_tx_id', ''),
            'balance'        : new_balance,
            'status'         : result.get('status', 'pending'),
            'fee'            : result.get('fee', 0.0),
            'note'           : result.get('note', ''),
        }

    # ── ПОПОЛНЕНИЕ ────────────────────────────────────────────────────────────

    @staticmethod
    def deposit(
        user_id: int,
        amount: float,
        currency: str,
        provider: str,
        meta: Optional[dict] = None,
    ) -> dict:
        """
        Инициировать пополнение баланса через провайдера.
        Средства зачисляются ПОСЛЕ подтверждения через webhook.

        Returns:
            {'ok': True, 'provider_tx_id': str, 'payment_url': str|None,
             'status': 'pending', 'liqpay_data': ..., 'liqpay_signature': ...}
        """
        meta = meta or {}
        cfg  = _load_config()

        if amount <= 0:
            raise WithdrawalError('Сумма пополнения должна быть положительной')

        min_d, _ = WithdrawalService._get_limits(cfg, currency)
        if amount < min_d:
            raise WithdrawalLimitError(f'Минимальная сумма пополнения: {min_d} {currency}')

        prov = get_provider(provider)
        prov._check_currency(currency)

        # Создаём транзакцию 'pending' — зачислим после webhook
        tx_id = 0
        with get_db() as db:
            try:
                tx_id = WithdrawalService._create_tx(
                    db, user_id, 'deposit', amount, currency, provider,
                    'pending', meta
                )
            except Exception:
                pass

        # Вызываем провайдер
        result = prov.deposit(user_id, amount, currency, meta)

        # Обновляем transaction_id от провайдера
        if tx_id and result.get('provider_tx_id'):
            with get_db() as db:
                WithdrawalService._update_tx(
                    db, tx_id, 'pending', result['provider_tx_id']
                )

        logger.info(
            f'[WithdrawalService] deposit initiated user={user_id} amount={amount} {currency} '
            f'provider={provider} tx={result.get("provider_tx_id")}'
        )

        return {
            'ok'             : True,
            'provider_tx_id' : result.get('provider_tx_id', ''),
            'status'         : result.get('status', 'pending'),
            'payment_url'    : result.get('payment_url'),
            'fee'            : result.get('fee', 0.0),
            'instructions'   : result.get('instructions'),       # для банк. перевода
            'liqpay_data'    : result.get('liqpay_data'),        # для LiqPay JS-форми
            'liqpay_signature': result.get('liqpay_signature'),
        }

    # ── ПОДТВЕРЖДЕНИЕ ЧЕРЕЗ WEBHOOK ────────────────────────────────────────────

    @staticmethod
    def confirm_deposit(provider_tx_id: str, amount: float, provider: str) -> bool:
        """
        Подтвердить пополнение после webhook / result_url от провайдера.
        Ищет транзакцию по order_id (= provider_tx_id для LiqPay) в meta.
        Идемпотентен — повторный вызов на уже completed транзакцию вернёт False.
        """
        from app.db import DB_ENGINE

        row = None
        with get_db() as db:
            if DB_ENGINE == 'postgresql':
                # PostgreSQL — поиск по JSONB
                db.execute(
                    """SELECT id, user_id, amount, status FROM transactions
                       WHERE type    = 'deposit'
                         AND status  = 'pending'
                         AND (meta->>'order_id' = %s OR meta->>'provider_tx_id' = %s)
                       LIMIT 1""",
                    (provider_tx_id, provider_tx_id),
                )
            else:
                # SQLite — JSON извлекается через json_extract
                db.execute(
                    """SELECT id, user_id, amount, status FROM transactions
                       WHERE type   = 'deposit'
                         AND status = 'pending'
                         AND (json_extract(meta, '$.order_id') = ?
                              OR json_extract(meta, '$.provider_tx_id') = ?)
                       LIMIT 1""",
                    (provider_tx_id, provider_tx_id),
                )
            row = db.fetchone()

        if not row:
            logger.warning(f'[WithdrawalService] confirm_deposit: tx not found for {provider_tx_id}')
            return False

        tx = dict(row)
        if tx['status'] != 'pending':
            logger.info(f'[WithdrawalService] confirm_deposit: tx already {tx["status"]} ({provider_tx_id})')
            return False

        # Зачисляем средства
        try:
            WalletService.deposit(
                tx['user_id'], float(tx['amount']),
                meta={'provider': provider, 'provider_tx_id': provider_tx_id}
            )
        except WalletError as e:
            logger.error(f'[WithdrawalService] confirm_deposit wallet error: {e}')
            return False

        # Обновляем статус транзакции
        with get_db() as db:
            db.execute(
                "UPDATE transactions SET status = 'completed' WHERE id = %s",
                (tx['id'],)
            )

        logger.info(
            f'[WithdrawalService] deposit confirmed  provider={provider} '
            f'tx={provider_tx_id} user={tx["user_id"]} amount={tx["amount"]}'
        )
        return True
