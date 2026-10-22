from app.bootstrap.models import BootstrapVersion  # noqa: F401
from app.db.base import Base
from app.modules.audit.models import LoginLog, OperationLog  # noqa: F401
from app.modules.config.models import DictItem, DictType, SystemConfig  # noqa: F401
from app.modules.content.models import FileObject, Notice, NoticeRead  # noqa: F401
from app.modules.notify.models import NotifyChannel, NotifyRecord  # noqa: F401
from app.modules.schedule.models import Job, JobLock, JobLog, JobTemplate  # noqa: F401
from app.modules.security.models import ApiKey, IpRule  # noqa: F401
from app.modules.system.models import Dept, Menu, Permission, Role, User, UserSession  # noqa: F401
from app.modules.theme.models import ThemeTemplate  # noqa: F401

metadata = Base.metadata
