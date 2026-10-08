"""Pure callback for an existing Telegram receiver. No outbound messages or I/O client."""
from datetime import datetime, timezone
import re

from .gates import assess, aware, identifier
from .models import Candidate, CommandRequest, ProviderRegistration, Reply, SPORTS

_COMMAND = re.compile(r"/(at|basket|futbol|hisse|durum|performans)(?:@([A-Za-z0-9_]{5,32}))?(?:\s+(.+))?\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class CommandDispatcher:
    def __init__(self, store, *, allowed_chat_ids, allowed_user_ids, providers=None,
                 bot_username=None, clock=None):
        self.store = store
        self.allowed_chats = frozenset(allowed_chat_ids)
        self.allowed_users = frozenset(allowed_user_ids)
        if (not self.allowed_chats or not self.allowed_users
                or any(type(i) is not int or i == 0 for i in self.allowed_chats)
                or any(type(i) is not int or i <= 0 for i in self.allowed_users)):
            raise ValueError("INVALID_ACCESS_ALLOWLIST")
        if bot_username is not None and (not isinstance(bot_username, str)
                or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username)):
            raise ValueError("INVALID_BOT_USERNAME")
        self.bot_username = bot_username.lower() if bot_username else None
        self.providers = dict(providers or {})
        for sport, registration in self.providers.items():
            if (sport not in SPORTS or not isinstance(registration, ProviderRegistration)
                    or not isinstance(registration.model, str) or not _MODEL.fullmatch(registration.model)
                    or not callable(registration.callback)
                    or not isinstance(registration.source_hosts, frozenset)
                    or any(not isinstance(h, str) or not re.fullmatch(r"[a-z0-9.-]+", h)
                           for h in registration.source_hosts)):
                raise ValueError("INVALID_PROVIDER_REGISTRATION")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def handle_update(self, update):
        """Return a plain-text Reply, or None when the existing handler should continue.

        Only ordinary authenticated user messages are considered. The receiver
        must pass an authentic Bot API update, not arbitrary HTTP/Telegram text
        decoded as JSON, and keep its existing transport authentication.
        """
        if not isinstance(update, dict) or type(update.get("update_id")) is not int or update["update_id"] < 0:
            return None
        message = update.get("message")
        if not isinstance(message, dict) or type(message.get("message_id")) is not int:
            return None
        if any(key in message for key in ("sender_chat", "forward_origin", "forward_from", "via_bot")):
            return None
        chat, sender = message.get("chat"), message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return None
        chat_id, user_id = chat.get("id"), sender.get("id")
        if (type(chat_id) is not int or type(user_id) is not int
                or chat_id not in self.allowed_chats or user_id not in self.allowed_users
                or sender.get("is_bot") is not False):
            return None
        text = message.get("text")
        if not isinstance(text, str) or len(text) > 512 or any(ord(c) < 32 and c != " " for c in text):
            return None
        match = _COMMAND.fullmatch(text.strip())
        if not match:
            return None
        command, mention, raw_args = match.groups()
        if mention and mention.lower() != self.bot_username:
            return None
        args = tuple((raw_args or "").split())
        if len(args) > 8 or any(len(arg) > 64 for arg in args):
            return Reply(chat_id, "PAS | INVALID_COMMAND_ARGUMENTS")
        if command in ("durum", "performans"):
            if args:
                return Reply(chat_id, "PAS | INVALID_COMMAND_ARGUMENTS")
            try:
                return self._status(chat_id) if command == "durum" else self._performance(chat_id)
            except Exception:
                return Reply(chat_id, "PAS | STORAGE_UNAVAILABLE")
        try:
            now = self.clock()
            if not aware(now):
                return Reply(chat_id, "PAS | CLOCK_UNAVAILABLE")
            registration = self.providers.get(command)
            model = registration.model if registration else "unconfigured-v1"
            candidate = Candidate(reason_codes=("PROVIDER_UNAVAILABLE",))
            message_time = message.get("date")
            age = now.timestamp() - message_time if type(message_time) is int else None
            if age is None or age < -30 or age > 300:
                candidate = Candidate(reason_codes=("COMMAND_TIME_UNVERIFIED",))
            elif registration:
                try:
                    candidate = registration.callback(CommandRequest(command, args, now))
                except Exception:
                    candidate = Candidate(reason_codes=("PROVIDER_UNAVAILABLE",))
            # Recheck after the provider returns: source/cutoff can expire during a read.
            checked_at = self.clock()
            if not aware(checked_at) or checked_at < now:
                return Reply(chat_id, "PAS | CLOCK_UNAVAILABLE")
            reasons, provenance = assess(candidate, command, checked_at,
                                         registration.source_hosts if registration else frozenset())
            if not isinstance(candidate, Candidate):
                candidate = Candidate()
            row = self.store.record_prediction(
                request_id=str(update["update_id"]), scope=str(chat_id), sport=command, model=model,
                event_id=candidate.event_id if identifier(candidate.event_id) else None,
                market=candidate.market if identifier(candidate.market) else None,
                closes_at=candidate.closes_at if aware(candidate.closes_at) else None,
                selection=candidate.selection if identifier(candidate.selection) and not reasons else None,
                reasons=reasons, now=checked_at, provenance=provenance,
            )
            if row["decision"] == "PREDICTION" and row.get("reused"):
                # A retry must not present a historical choice as a fresh signal
                # even if the provider now returns a different, fresh event.
                # Preserve the original ledger weight; do not repeat a choice.
                codes = reasons or ("PREVIOUS_RECORD_EXISTS",)
                return Reply(chat_id, f"/{command} | PAS | {', '.join(codes)} | Önceki kayıt korunuyor.")
            return self._prediction_reply(chat_id, row)
        except Exception:
            # No exception messages, raw provider values or credentials reach Telegram.
            return Reply(chat_id, "PAS | STORAGE_OR_PROVIDER_UNAVAILABLE")

    def _prediction_reply(self, chat_id, row):
        if row["decision"] == "PAS":
            return Reply(chat_id, f"/{row['sport']} | PAS | {', '.join(row['reasons'])}")
        label = "İZLE (gözlemsel tahmin)" if row["sport"] == "hisse" else "TAHMİN"
        lines = [f"/{row['sport']} | {label} | {row['selection']}",
                 f"Kayıt zamanı (UTC): {row['created_at']}",
                 f"Model: {row['model']} | Olay: {row['event_id']} | Pazar: {row['market']}",
                 f"Kapanış/hedef (UTC): {row['closes_at']}", "Sonuç: BEKLİYOR"]
        for source in row.get("provenance", ()):
            lines.append(f"Kaynak: {source['source_id']} | zaman: {source['as_of']} | "
                         f"gecikme: {source.get('delay_seconds', 'bilinmiyor')} sn")
        if row["sport"] == "hisse":
            lines.append("Emir yok. Sermaye 50.000 TL; pozisyon en çok 12.500 TL; "
                         "planlanan stop %3; günlük zarar eşiği 2.500 TL.")
        return Reply(chat_id, "\n".join(lines))

    def _status(self, chat_id):
        lines = ["execution_enabled=false", "Telegram alıcısı: mevcut alıcıya callback",
                 "Sağlayıcı kaydı, veri doğrulaması anlamına gelmez."]
        for sport in SPORTS:
            registration = self.providers.get(sport)
            lines.append(f"/{sport}: {registration.model if registration else 'PAS / PROVIDER_UNAVAILABLE'}")
        lines.append("AGF kullanılmaz. Zorunlu veri eksik/bayat/doğrulanmamışsa PAS.")
        return Reply(chat_id, "\n".join(lines))

    def _performance(self, chat_id):
        rows = self.store.stats(str(chat_id))
        if not rows:
            return Reply(chat_id, "Henüz tahmin kaydı yok. Başarı: — (doğrulanmış sonuç yok).")
        lines = ["Başarı = doğru / (doğru + yanlış). PAS, bekleyen ve iptal hariç."]
        for row in rows:
            settled = row["correct"] + row["incorrect"]
            rate = f"%{100 * row['correct'] / settled:.1f}" if settled else "—"
            lines.append(f"{row['sport']} / {row['model']}: {rate} | doğru {row['correct']} | "
                         f"yanlış {row['incorrect']} | PAS {row['pas']} | bekleyen {row['pending']} | "
                         f"iptal {row['void']}")
        # Telegram has a 4096-character limit. Do not silently lose entire model rows.
        text = "\n".join(lines)
        if len(text) > 4000:
            return Reply(chat_id, "PERFORMANCE_REPORT_TOO_LARGE | Tam branş/model raporu yerel store.stats() ile okunabilir.")
        return Reply(chat_id, text)
