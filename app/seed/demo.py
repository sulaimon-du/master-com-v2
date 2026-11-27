"""Demo master accounts for staging (R-027): `python -m app.cli demo seed|remove`.

Ten clearly fake but filled master profiles (and three demo clients with one open request each),
so search, the SEO pages and the feed have content. Everything is built through the normal
services, so slugs, publishing and the search index stay consistent. Demo accounts are found by
their email (`demo-<n>@ustoiman.invalid`, `demo-client-<n>@ustoiman.invalid`; the `.invalid`
TLD can never receive mail). They must be removed before any real launch.

The texts below are seed data, not UI, so they are Russian literals here, not locale keys.
Callers commit; files written to storage are reported so a failed run can clean them up.
"""

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.core.time import utcnow
from app.models import (
    City,
    Complaint,
    District,
    Job,
    JobRequest,
    JobResponse,
    MasterProfile,
    NotificationPref,
    PortfolioCollection,
    PortfolioPhoto,
    ProfileClaim,
    RequestPhoto,
    Review,
    Specialty,
    SupportMessage,
    SupportThread,
    User,
)
from app.models.jobs import CHANNELS, EVENTS
from app.schemas.requests import RequestInput
from app.seed import demo_photos
from app.services import masters, portfolio, requests
from app.services.images import ImageError, process_avatar
from app.services.storage import Storage, new_key
from app.services.translit import translit

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_FILE = ROOT / ".superpowers" / "demo-accounts.txt"
CACHE_DIR = ROOT / ".cache" / "demo_photos"
DOMAIN = "ustoiman.invalid"
EMAIL_RE = r"^demo-(client-)?[0-9]+@ustoiman\.invalid$"
# Publishing needs a verified phone (R-022 D3). Demo masters get numbers from the UK Ofcom range
# reserved for TV and film (+44 7700 900000-900999): it is never assigned, so no real person can
# be reached through a demo profile (R-027).
PHONE_PREFIX = "+447700900"


class DemoError(Exception):
    """The demo command cannot run; the message is safe to print (no secrets, no personal data)."""


@dataclass(frozen=True)
class Work:
    title: str
    description: str
    photos: int
    topic: str  # a specialty slug: which manifest photos (or generated pattern) fit


@dataclass(frozen=True)
class DemoMaster:
    name: str
    headline: str
    bio: str
    specialties: tuple[str, ...]  # the first one is primary
    city: str
    district: str | None
    experience: int
    works: tuple[Work, ...]


@dataclass(frozen=True)
class DemoClient:
    name: str
    city: str
    district: str | None
    specialties: tuple[str, ...]
    description: str


MASTERS: tuple[DemoMaster, ...] = (
    DemoMaster(
        "Фаррух Назаров",
        "Плиточник, ванные и кухни под ключ",
        "Укладываю керамическую плитку и керамогранит в ванных, санузлах и на кухнях. "
        "Работаю аккуратно, швы ровные, после себя убираю. Помогу рассчитать материал.",
        ("plitochnik", "remont-kvartir"),
        "dushanbe",
        "sino",
        12,
        (
            Work(
                "Ванная комната в новостройке",
                "Стены и пол, плитка 30×60, затирка в тон.",
                3,
                "plitochnik",
            ),
            Work("Фартук на кухне", "Кабанчик, раскладка ёлочкой.", 2, "plitochnik"),
            Work(
                "Санузел под ключ",
                "Демонтаж старой плитки, выравнивание, новая плитка.",
                2,
                "plitochnik",
            ),
            Work("Пол в прихожей", "Керамогранит 60×60 с подрезкой по месту.", 1, "plitochnik"),
        ),
    ),
    DemoMaster(
        "Бахтиёр Саидов",
        "Каменщик: кирпич, блоки, заборы",
        "Кладу стены из кирпича и блоков, делаю перегородки и заборы. Опыт на частных домах "
        "и пристройках. Соблюдаю перевязку и вертикаль.",
        ("kamenshchik", "fundament"),
        "dushanbe",
        "firdavsi",
        15,
        (
            Work(
                "Стены частного дома",
                "Кладка из жжёного кирпича в полтора кирпича.",
                3,
                "kamenshchik",
            ),
            Work("Забор из кирпича", "Столбы и пролёты, длина 40 м.", 2, "kamenshchik"),
            Work(
                "Перегородки в квартире",
                "Газоблок 100 мм, армирование каждые три ряда.",
                2,
                "kamenshchik",
            ),
        ),
    ),
    DemoMaster(
        "Далер Каримов",
        "Штукатур-маляр, ровные стены",
        "Штукатурю стены по маякам, шпаклюю и крашу. Работаю с гипсовыми и цементными смесями. "
        "Подготовлю стены под обои или покраску.",
        ("shtukatur", "remont-kvartir"),
        "khujand",
        None,
        9,
        (
            Work(
                "Штукатурка по маякам",
                "Двухкомнатная квартира, гипсовая штукатурка.",
                2,
                "shtukatur",
            ),
            Work(
                "Покраска стен в спальне",
                "Шпаклёвка в два слоя и покраска в светлый тон.",
                2,
                "remont-kvartir",
            ),
            Work("Откосы и потолок", "Выравнивание и покраска.", 1, "shtukatur"),
            Work("Гостиная", "Декоративная покраска одной стены.", 2, "remont-kvartir"),
        ),
    ),
    DemoMaster(
        "Шерзод Олимов",
        "Сантехник: трубы, смесители, бойлеры",
        "Меняю трубы, ставлю смесители, унитазы и водонагреватели. Развожу воду и канализацию "
        "в квартирах и домах. Выезжаю в день обращения, если есть время.",
        ("santekhnik", "vodoprovod", "vodonagrevateli"),
        "dushanbe",
        "ismoili-somoni",
        10,
        (
            Work("Разводка труб в ванной", "Полипропилен, скрытая разводка.", 3, "santekhnik"),
            Work("Замена стояка", "Металл на полипропилен, без протечек.", 2, "santekhnik"),
            Work(
                "Установка бойлера", "Водонагреватель на 80 литров с подключением.", 1, "santekhnik"
            ),
            Work("Кухонная мойка", "Сифон, смеситель и подключение.", 1, "santekhnik"),
            Work("Санузел в частном доме", "Вода и канализация с нуля.", 2, "santekhnik"),
        ),
    ),
    DemoMaster(
        "Комрон Мирзоев",
        "Электрик: проводка и щиты",
        "Делаю электропроводку в квартирах и домах, собираю щиты, ставлю розетки и светильники. "
        "Работаю по правилам безопасности, все соединения проверяю.",
        ("elektrik", "osveshchenie"),
        "dushanbe",
        "shohmansur",
        8,
        (
            Work("Электрощит в квартире", "Автоматы и УЗО, маркировка линий.", 2, "elektrik"),
            Work("Новая проводка", "Замена алюминиевой проводки на медную.", 3, "elektrik"),
            Work("Освещение кухни", "Точечные светильники и подсветка.", 1, "elektrik"),
        ),
    ),
    DemoMaster(
        "Сухроб Юсупов",
        "Плотник и столяр",
        "Делаю деревянные конструкции: каркасы, перекрытия, лестницы и айваны. "
        "Работаю с сухим деревом, всё обрабатываю от влаги.",
        ("plotnik", "stolyar"),
        "bokhtar",
        None,
        14,
        (
            Work("Каркас пристройки", "Деревянный каркас и обрешётка.", 2, "plotnik"),
            Work("Деревянное перекрытие", "Балки и настил второго этажа.", 2, "plotnik"),
            Work("Терраса", "Настил из лиственницы.", 2, "plotnik"),
            Work("Лестница на второй этаж", "Сосна, покрытие лаком.", 1, "plotnik"),
        ),
    ),
    DemoMaster(
        "Умед Шарипов",
        "Кровельщик: металл и шифер",
        "Кладу кровлю из профнастила, металлочерепицы и шифера, меняю старую кровлю. "
        "Делаю водостоки и утепление.",
        ("krovelshchik",),
        "vahdat",
        None,
        11,
        (
            Work("Кровля частного дома", "Металлочерепица, площадь 160 м².", 3, "krovelshchik"),
            Work("Замена шифера", "Демонтаж и новый профнастил.", 2, "krovelshchik"),
            Work("Ремонт кровли после ветра", "Замена листов и коньков.", 1, "krovelshchik"),
        ),
    ),
    DemoMaster(
        "Джамшед Хакимов",
        "Полы: ламинат, паркет, стяжка",
        "Укладываю ламинат, паркетную доску и линолеум, делаю стяжку. Подготовлю основание, "
        "поставлю плинтусы и пороги.",
        ("polovshchik", "remont-kvartir"),
        "khujand",
        None,
        7,
        (
            Work(
                "Ламинат в трёхкомнатной квартире", "Подложка и плинтусы в тон.", 2, "polovshchik"
            ),
            Work("Паркетная доска", "Укладка в гостиной, 35 м².", 2, "polovshchik"),
            Work("Стяжка пола", "Полусухая стяжка с выравниванием.", 1, "polovshchik"),
        ),
    ),
    DemoMaster(
        "Рустам Давлатов",
        "Кухни и мебель на заказ",
        "Изготавливаю и устанавливаю кухни и шкафы по размерам. Помогаю выбрать материалы и "
        "фурнитуру. Замер и проект бесплатно.",
        ("kukhni", "mebelshchik"),
        "dushanbe",
        None,
        13,
        (
            Work("Угловая кухня", "МДФ, столешница из искусственного камня.", 3, "kukhni"),
            Work("Кухня в светлых тонах", "Фасады без ручек.", 2, "kukhni"),
            Work("Кухня с островом", "Частный дом, 14 м².", 2, "kukhni"),
            Work("Встроенный шкаф", "Шкаф-купе в прихожей.", 1, "kukhni"),
        ),
    ),
    DemoMaster(
        "Фирӯз Рахимов",
        "Ремонт квартир под ключ",
        "Делаю ремонт квартир от черновой отделки до чистовой. Работаю с бригадой: штукатурка, "
        "плитка, полы, покраска. Составлю смету до начала работ.",
        ("remont-kvartir", "malyar", "plitochnik"),
        "dushanbe",
        "sino",
        16,
        (
            Work("Двухкомнатная квартира", "Полный ремонт за шесть недель.", 3, "remont-kvartir"),
            Work("Покраска стен", "Подготовка и покраска коридора.", 2, "remont-kvartir"),
            Work("Ванная под ключ", "Плитка, сантехника, потолок.", 2, "plitochnik"),
            Work("Кухня-гостиная", "Перепланировка и отделка.", 2, "remont-kvartir"),
            Work("Детская комната", "Ремонт и покраска.", 1, "remont-kvartir"),
            Work("Балкон", "Утепление и отделка.", 1, "remont-kvartir"),
        ),
    ),
)

CLIENTS: tuple[DemoClient, ...] = (
    DemoClient(
        "Мадина Ахмедова",
        "dushanbe",
        "sino",
        ("plitochnik",),
        "Нужно уложить плитку в ванной комнате: примерно 12 м² стены и 4 м² пол. "
        "Плитка уже куплена, старой плитки нет.",
    ),
    DemoClient(
        "Нигина Сафарова",
        "dushanbe",
        "ismoili-somoni",
        ("santekhnik",),
        "Заменить смеситель на кухне и подключить новый водонагреватель на 50 литров.",
    ),
    DemoClient(
        "Зарина Холова",
        "khujand",
        None,
        ("malyar", "shtukatur"),
        "Подготовить и покрасить стены в двухкомнатной квартире, около 90 м² стен.",
    ),
)


def master_email(n: int) -> str:
    return f"demo-{n}@{DOMAIN}"


def client_email(n: int) -> str:
    return f"demo-client-{n}@{DOMAIN}"


def demo_user_ids(db: Session) -> list[int]:
    return list(db.scalars(select(User.id).where(User.email.regexp_match(EMAIL_RE))))


# --- password file ----------------------------------------------------------------------------


def _read_password(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("password: "):
            return line.removeprefix("password: ").strip() or None
    return None


def _write_accounts(path: Path, password: str, emails: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# UstoiMan demo accounts (R-027). Git-ignored; never commit or share.\n"
        f"password: {password}\n" + "".join(f"{email}\n" for email in emails)
    )
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(body, encoding="utf-8")


# --- seed -------------------------------------------------------------------------------------


@dataclass
class SeedStats:
    created: int = 0
    kept: int = 0
    clients_created: int = 0
    clients_kept: int = 0
    photos: int = 0
    real_photos: int = 0
    generated_photos: int = 0
    written: list[str] = field(default_factory=list)  # storage keys, for cleanup on failure


class _Recording:
    """Storage wrapper that remembers every key written, so a failed run can delete them."""

    def __init__(self, inner: Storage, written: list[str]) -> None:
        self.inner, self.written = inner, written

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.inner.put(key, data, content_type)
        self.written.append(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def url(self, key: str) -> str:
        return self.inner.url(key)


class _Photos:
    """Hands out photo bytes per topic: manifest photos in turn, generated ones as fallback."""

    def __init__(self, manifest: dict[str, list[dict]], download: demo_photos.Downloader) -> None:
        self.manifest, self.download = manifest, download
        self.turn: dict[str, int] = {}
        self.failed: set[str] = set()

    def candidates(self, topic: str) -> list[dict]:
        return [e for e in self.manifest.get(topic, []) if e["url"] not in self.failed]

    def next(self, topic: str) -> tuple[bytes, bool]:
        """(image bytes, is it a real photo)."""
        n = self.turn.get(topic, 0)
        self.turn[topic] = n + 1
        options = self.candidates(topic)
        if options:
            entry = options[n % len(options)]
            try:
                return self.download(entry["url"]), True
            except Exception:  # any failure means "use the fallback"
                self.failed.add(entry["url"])
        return demo_photos.generated_photo(topic, n), False


def _ids_by_slug(db: Session, model, slugs) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return dict(db.execute(select(model.slug, model.id).where(model.slug.in_(set(slugs)))).all())


def _initials(name: str) -> str:
    return "".join(part[0] for part in translit(name).split()[:2]).upper()


def _opt_out(db: Session, user: User) -> None:
    """Demo accounts get no notifications: their addresses cannot receive mail anyway."""
    db.add_all(
        NotificationPref(user_id=user.id, event=e, channel=c, enabled=False)
        for e in EVENTS
        for c in CHANNELS
    )


def _new_user(db: Session, email: str, name: str, password_hash: str, phone: str | None) -> User:
    now = utcnow()
    user = User(
        email=email,
        name=name,
        password_hash=password_hash,
        email_verified_at=now,
        phone=phone,
        phone_verified_at=now if phone else None,
        locale="ru",
    )
    db.add(user)
    db.flush()
    _opt_out(db, user)
    return user


def _existing(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and not verify_password(user.password_hash, password):
        user.password_hash = hash_password(password)  # keep the accounts file truthful
    return user


def _seed_master(
    db: Session,
    storage: Storage,
    photos: _Photos,
    n: int,
    spec: DemoMaster,
    password_hash: str,
    stats: SeedStats,
) -> None:
    user = _new_user(db, master_email(n), spec.name, password_hash, f"{PHONE_PREFIX}{n:03d}")
    data = masters.BasicsInput(
        display_name=spec.name,
        slug=masters.suggest_slug(db, spec.name),
        headline=spec.headline,
        experience_years=spec.experience,
        is_available=True,
    )
    master = masters.create_profile(db, user, data)
    masters.update_basics(db, master, data, bio=spec.bio)

    specialty_ids = _ids_by_slug(db, Specialty, spec.specialties)
    if set(specialty_ids) != set(spec.specialties):
        raise DemoError("the catalog is not seeded; run `python -m app.seed` first")
    ordered = [specialty_ids[s] for s in spec.specialties]
    masters.set_specialties(db, master, ordered, ordered[:1])

    city_id = _ids_by_slug(db, City, [spec.city]).get(spec.city)
    if city_id is None:
        raise DemoError("the places are not seeded; run `python -m app.seed` first")
    district_ids = []
    if spec.district is not None:
        district_ids = [
            db.scalars(
                select(District.id).where(
                    District.slug == spec.district, District.city_id == city_id
                )
            ).one()
        ]
    masters.set_areas(db, master, [] if district_ids else [city_id], district_ids)

    avatar = process_avatar(demo_photos.avatar(_initials(spec.name), n))
    key = new_key("a")
    storage.put(key, avatar.data, "image/webp")
    masters.set_avatar(db, master, key)

    for work in spec.works:
        collection = portfolio.create_collection(db, master, work.title, work.description)
        for _ in range(work.photos):
            data_bytes, real = photos.next(work.topic)
            try:
                portfolio.add_photo(db, storage, master, collection.id, data_bytes)
            except ImageError:
                if not real:
                    raise
                # A real photo the pipeline refuses: the generated picture takes its place.
                data_bytes, real = demo_photos.generated_photo(work.topic, stats.photos), False
                portfolio.add_photo(db, storage, master, collection.id, data_bytes)
            stats.photos += 1
            stats.real_photos += real
            stats.generated_photos += not real
    masters.refresh_publication(db, master)
    if master.published_at is None:
        raise DemoError("a demo master could not be published")


def _seed_client(db: Session, n: int, spec: DemoClient, password_hash: str) -> None:
    user = _new_user(db, client_email(n), spec.name, password_hash, None)
    city_id = _ids_by_slug(db, City, [spec.city])[spec.city]
    district_id = None
    if spec.district is not None:
        district_id = db.scalars(
            select(District.id).where(District.slug == spec.district, District.city_id == city_id)
        ).one()
    specialty_ids = _ids_by_slug(db, Specialty, spec.specialties)
    request = requests.create_request(
        db,
        user,
        RequestInput(
            specialty_ids=[specialty_ids[s] for s in spec.specialties],
            city_id=city_id,
            district_id=district_id,
            description=spec.description,
            timing="flexible",
        ),
    )
    # Demo requests must not fan out to real masters' Telegram or email: drop the job.
    db.execute(delete(Job).where(Job.dedupe_key == f"new_request:{request.id}"))


def seed(
    db: Session,
    storage: Storage,
    *,
    count: int = len(MASTERS),
    accounts_file: Path = ACCOUNTS_FILE,
    download: demo_photos.Downloader | None = None,
    manifest: dict[str, list[dict]] | None = None,
    clients: bool = True,
    written: list[str] | None = None,
) -> SeedStats:
    """Create the missing demo accounts; existing ones are kept. Callers commit.

    The shared password is read from `accounts_file` or generated, and written only there.
    Every storage key written is appended to `written` as it happens, so a caller whose
    transaction fails can delete the files again.
    """
    if not 1 <= count <= len(MASTERS):
        raise DemoError(f"--count must be between 1 and {len(MASTERS)}")
    stats = SeedStats(written=[] if written is None else written)
    recording = _Recording(storage, stats.written)
    password = _read_password(accounts_file) or secrets.token_urlsafe(12)
    password_hash = hash_password(password)
    photos = _Photos(
        demo_photos.load_manifest() if manifest is None else manifest,
        download or demo_photos.cached_downloader(CACHE_DIR),
    )
    for n, spec in enumerate(MASTERS[:count], start=1):
        if _existing(db, master_email(n), password) is not None:
            stats.kept += 1
            continue
        _seed_master(db, recording, photos, n, spec, password_hash, stats)
        stats.created += 1
    for n, spec in enumerate(CLIENTS if clients else (), start=1):
        if _existing(db, client_email(n), password) is not None:
            stats.clients_kept += 1
            continue
        _seed_client(db, n, spec, password_hash)
        stats.clients_created += 1
    db.flush()
    emails = db.scalars(select(User.email).where(User.email.regexp_match(EMAIL_RE))).all()
    _write_accounts(accounts_file, password, sorted(e for e in emails if e))
    return stats


# --- remove -----------------------------------------------------------------------------------


@dataclass
class RemoveStats:
    users: int = 0
    masters: int = 0
    photos: int = 0
    requests: int = 0
    keys: list[str] = field(default_factory=list)  # storage files to delete after the commit


def remove(db: Session) -> RemoveStats:
    """Delete every demo account and everything hanging off it. Callers commit, then delete
    `keys` from storage (see `delete_files`)."""
    stats = RemoveStats()
    user_ids = demo_user_ids(db)
    if not user_ids:
        return stats
    master_ids = list(
        db.scalars(select(MasterProfile.id).where(MasterProfile.user_id.in_(user_ids)))
    )
    request_ids = list(db.scalars(select(JobRequest.id).where(JobRequest.client_id.in_(user_ids))))

    # A real client's request that chose a demo master (or a real review of one) would lose its
    # master: refuse rather than touch real data. The owner closes those requests first.
    blocking = (
        db.scalar(
            select(func.count()).where(
                JobRequest.chosen_master_id.in_(master_ids), JobRequest.client_id.not_in(user_ids)
            )
        )
        or 0
    )
    blocking += (
        db.scalar(
            select(func.count()).where(
                Review.master_id.in_(master_ids), Review.author_id.not_in(user_ids)
            )
        )
        or 0
    )
    if blocking:
        raise DemoError(
            f"{blocking} real request(s) or review(s) point at a demo master; nothing was removed"
        )

    # Responses by demo masters on real requests: keep those requests' counters right.
    response_rows = db.execute(
        select(JobResponse.id, JobResponse.request_id, JobResponse.withdrawn_at).where(
            or_(JobResponse.master_id.in_(master_ids), JobResponse.request_id.in_(request_ids))
        )
    ).all()
    response_ids = [r.id for r in response_rows]
    active_on_real: dict[int, int] = {}
    for r in response_rows:
        if r.request_id not in request_ids and r.withdrawn_at is None:
            active_on_real[r.request_id] = active_on_real.get(r.request_id, 0) + 1
    for request_id, n in active_on_real.items():
        db.execute(
            update(JobRequest)
            .where(JobRequest.id == request_id)
            .values(responses_count=func.greatest(JobRequest.responses_count - n, 0))
        )

    job_filters = [Job.payload["request_id"].as_integer().in_(request_ids)]
    if response_ids:
        job_filters.append(Job.payload["response_id"].as_integer().in_(response_ids))
    db.execute(delete(Job).where(or_(*job_filters)))

    review_ids = list(
        db.scalars(
            select(Review.id).where(
                or_(
                    Review.master_id.in_(master_ids),
                    Review.request_id.in_(request_ids),
                    Review.author_id.in_(user_ids),
                )
            )
        )
    )
    db.execute(
        delete(Complaint).where(
            or_(
                Complaint.reporter_id.in_(user_ids),
                Complaint.request_id.in_(request_ids),
                Complaint.review_id.in_(review_ids),
            )
        )
    )
    db.execute(delete(Review).where(Review.id.in_(review_ids)))

    for row in db.execute(
        select(RequestPhoto.storage_key, RequestPhoto.thumb_key).where(
            RequestPhoto.request_id.in_(request_ids)
        )
    ):
        stats.keys += [row.storage_key, row.thumb_key]
    db.execute(delete(JobResponse).where(JobResponse.id.in_(response_ids)))
    stats.requests = len(request_ids)
    db.execute(delete(JobRequest).where(JobRequest.id.in_(request_ids)))

    photo_rows = db.execute(
        select(PortfolioPhoto.storage_key, PortfolioPhoto.thumb_key)
        .join(PortfolioCollection, PortfolioCollection.id == PortfolioPhoto.collection_id)
        .where(PortfolioCollection.master_id.in_(master_ids))
    ).all()
    stats.photos = len(photo_rows)
    for row in photo_rows:
        stats.keys += [row.storage_key, row.thumb_key]
    stats.keys += [
        k
        for k in db.scalars(
            select(MasterProfile.avatar_key).where(MasterProfile.id.in_(master_ids))
        )
        if k
    ]

    db.execute(delete(SupportMessage).where(SupportMessage.author_id.in_(user_ids)))
    db.execute(delete(SupportThread).where(SupportThread.user_id.in_(user_ids)))
    db.execute(delete(ProfileClaim).where(ProfileClaim.user_id.in_(user_ids)))
    # Cascades: search row, specialties, areas, collections, photos, complaints about them.
    db.execute(delete(MasterProfile).where(MasterProfile.id.in_(master_ids)))
    # Cascades: sessions, tokens, OAuth flows, Google claims, notification settings.
    db.execute(delete(User).where(User.id.in_(user_ids)))
    db.flush()
    db.expire_all()
    stats.users, stats.masters = len(user_ids), len(master_ids)
    return stats


def delete_files(storage: Storage, keys: list[str]) -> int:
    """Best effort after the commit; returns how many deletes failed."""
    failed = 0
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # one bad key must not stop the rest
            failed += 1
    return failed


def describe(stats: SeedStats) -> str:
    return (
        f"demo masters: {stats.created} created, {stats.kept} kept; "
        f"demo clients: {stats.clients_created} created, {stats.clients_kept} kept; "
        f"portfolio photos: {stats.photos} ({stats.real_photos} real, "
        f"{stats.generated_photos} generated)"
    )
