import json
import re

from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from cleo.helpers import argument
from cleo.helpers import option

from poetry.console.commands.command import Command


if TYPE_CHECKING:
    from poetry.config.config_source import ConfigSource


class ConfigCommand(Command):

    name = "config"
    description = "Manages configuration settings."

    arguments = [
        argument("key", "Setting key.", optional=True),
        argument("value", "Setting value.", optional=True, multiple=True),
    ]

    options = [
        option("list", None, "List configuration settings."),
        option("unset", None, "Unset configuration setting."),
        option("local", None, "Set/Get from the project's local configuration."),
    ]

    help = """This command allows you to edit the poetry config settings and repositories.

To add a repository:

    <comment>poetry config repositories.foo https://bar.com/simple/</comment>

To remove a repository (repo is a short alias for repositories):

    <comment>poetry config --unset repo.foo</comment>"""

    LIST_PROHIBITED_SETTINGS = {"http-basic", "pypi-token"}

    @property
    def unique_config_values(self) -> Dict[str, Tuple[Any, Any, Any]]:
        from pathlib import Path

        from poetry.config.config import boolean_normalizer
        from poetry.config.config import boolean_validator
        from poetry.config.config import int_normalizer
        from poetry.locations import CACHE_DIR

        unique_config_values = {
            "cache-dir": (
                str,
                lambda val: str(Path(val)),
                str(Path(CACHE_DIR) / "virtualenvs"),
            ),
            "virtualenvs.create": (boolean_validator, boolean_normalizer, True),
            "virtualenvs.in-project": (boolean_validator, boolean_normalizer, False),
            "virtualenvs.options.always-copy": (
                boolean_validator,
                boolean_normalizer,
                False,
            ),
            "virtualenvs.options.system-site-packages": (
                boolean_validator,
                boolean_normalizer,
                False,
            ),
            "virtualenvs.path": (
                str,
                lambda val: str(Path(val)),
                str(Path(CACHE_DIR) / "virtualenvs"),
            ),
            "virtualenvs.prefer-active-python": (
                boolean_validator,
                boolean_normalizer,
                False,
            ),
            "experimental.new-installer": (
                boolean_validator,
                boolean_normalizer,
                True,
            ),
            "installer.parallel": (
                boolean_validator,
                boolean_normalizer,
                True,
            ),
            "installer.max-workers": (
                lambda val: int(val) > 0,
                int_normalizer,
                None,
            ),
        }

        return unique_config_values

    def handle(self) -> Optional[int]:
        from pathlib import Path

        from poetry.core.pyproject.exceptions import PyProjectException
        from poetry.core.toml.file import TOMLFile

        from poetry.config.file_config_source import FileConfigSource
        from poetry.factory import Factory
        from poetry.locations import CONFIG_DIR

        config = Factory.create_config(self.io)
        config_file = TOMLFile(Path(CONFIG_DIR) / "config.toml")

        try:
            local_config_file = TOMLFile(self.poetry.file.parent / "poetry.toml")
            if local_config_file.exists():
                config.merge(local_config_file.read())
        except (RuntimeError, PyProjectException):
            local_config_file = TOMLFile(Path.cwd() / "poetry.toml")

        if self.option("local"):
            config.set_config_source(FileConfigSource(local_config_file))

        if not config_file.exists():
            config_file.path.parent.mkdir(parents=True, exist_ok=True)
            config_file.touch(mode=0o0600)

        if self.option("list"):
            self._list_configuration(config.all(), config.raw())

            return 0

        setting_key = self.argument("key")
        if not setting_key:
            return 0

        if self.argument("value") and self.option("unset"):
            raise RuntimeError("You can not combine a setting value with --unset")

        # show the value if no value is provided
        if not self.argument("value") and not self.option("unset"):
            m = re.match(r"^repos?(?:itories)?(?:\.(.+))?", self.argument("key"))
            if m:
                if not m.group(1):
                    value = {}
                    if config.get("repositories") is not None:
                        value = config.get("repositories")
                else:
                    repo = config.get(f"repositories.{m.group(1)}")
                    if repo is None:
                        raise ValueError(f"There is no {m.group(1)} repository defined")

                    value = repo

                self.line(str(value))
            else:
                values = self.unique_config_values
                if setting_key not in values:
                    raise ValueError(f"There is no {setting_key} setting.")

                value = config.get(setting_key)

                if not isinstance(value, str):
                    value = json.dumps(value)

                self.line(value)

            return 0

        values = self.argument("value")

        unique_config_values = self.unique_config_values
        if setting_key in unique_config_values:
            if self.option("unset"):
                config.config_source.remove_property(setting_key)
                return None

            return self._handle_single_value(
                config.config_source,
                setting_key,
                unique_config_values[setting_key],
                values,
            )

        # handle repositories
        m = re.match(r"^repos?(?:itories)?(?:\.(.+))?", self.argument("key"))
        if m:
            if not m.group(1):
                raise ValueError("You cannot remove the [repositories] section")

            if self.option("unset"):
                repo = config.get(f"repositories.{m.group(1)}")
                if repo is None:
                    raise ValueError(f"There is no {m.group(1)} repository defined")

                config.config_source.remove_property(f"repositories.{m.group(1)}")

                return 0

            if len(values) == 1:
                url = values[0]

                config.config_source.add_property(f"repositories.{m.group(1)}.url", url)

                return 0

            raise ValueError(
                "You must pass the url. "
                "Example: poetry config repositories.foo https://bar.com"
            )

        # handle auth
        m = re.match(r"^(http-basic|pypi-token)\.(.+)", self.argument("key"))
        if m:
            from poetry.utils.password_manager import PasswordManager

            password_manager = PasswordManager(config)
            if self.option("unset"):
                if m.group(1) == "http-basic":
                    password_manager.delete_http_password(m.group(2))
                elif m.group(1) == "pypi-token":
                    password_manager.delete_pypi_token(m.group(2))

                return 0

            if m.group(1) == "http-basic":
                if len(values) == 1:
                    username = values[0]
                    # Only username, so we prompt for password
                    password = self.secret("Password:")
                elif len(values) != 2:
                    raise ValueError(
                        "Expected one or two arguments "
                        f"(username, password), got {len(values)}"
                    )
                else:
                    username = values[0]
                    password = values[1]

                password_manager.set_http_password(m.group(2), username, password)
            elif m.group(1) == "pypi-token":
                if len(values) != 1:
                    raise ValueError(
                        f"Expected only one argument (token), got {len(values)}"
                    )

                token = values[0]

                password_manager.set_pypi_token(m.group(2), token)

            return 0

        # handle certs
        m = re.match(
            r"(?:certificates)\.([^.]+)\.(cert|client-cert)", self.argument("key")
        )
        if m:
            if self.option("unset"):
                config.auth_config_source.remove_property(
                    f"certificates.{m.group(1)}.{m.group(2)}"
                )

                return 0

            if len(values) == 1:
                config.auth_config_source.add_property(
                    f"certificates.{m.group(1)}.{m.group(2)}", values[0]
                )
            else:
                raise ValueError("You must pass exactly 1 value")

            return 0

        raise ValueError(f"Setting {self.argument('key')} does not exist")

    def _handle_single_value(
        self,
        source: "ConfigSource",
        key: str,
        callbacks: Tuple[Any, Any, Any],
        values: List[Any],
    ) -> int:
        validator, normalizer, _ = callbacks

        if len(values) > 1:
            raise RuntimeError("You can only pass one value.")

        value = values[0]
        if not validator(value):
            raise RuntimeError(f'"{value}" is an invalid value for {key}')

        source.add_property(key, normalizer(value))

        return 0

    def _list_configuration(self, config: Dict, raw: Dict, k: str = "") -> None:
        orig_k = k
        for key, value in sorted(config.items()):
            if k + key in self.LIST_PROHIBITED_SETTINGS:
                continue

            raw_val = raw.get(key)

            if isinstance(value, dict):
                k += f"{key}."
                self._list_configuration(value, raw_val, k=k)
                k = orig_k

                continue
            elif isinstance(value, list):
                value = ", ".join(
                    json.dumps(val) if isinstance(val, list) else val for val in value
                )
                value = f"[{value}]"

            if k.startswith("repositories."):
                message = f"<c1>{k + key}</c1> = <c2>{json.dumps(raw_val)}</c2>"
            elif isinstance(raw_val, str) and raw_val != value:
                message = (
                    f"<c1>{k + key}</c1> = <c2>{json.dumps(raw_val)}</c2>  # {value}"
                )
            else:
                message = f"<c1>{k + key}</c1> = <c2>{json.dumps(value)}</c2>"

            self.line(message)

    def _get_setting(
        self,
        contents: Dict,
        setting: Optional[str] = None,
        k: Optional[str] = None,
        default: Optional[Any] = None,
    ) -> List[Tuple[str, str]]:
        orig_k = k

        if setting and setting.split(".")[0] not in contents:
            value = json.dumps(default)

            return [((k or "") + setting, value)]
        else:
            values = []
            for key, value in contents.items():
                if setting and key != setting.split(".")[0]:
                    continue

                if isinstance(value, dict) or key == "repositories" and k is None:
                    if k is None:
                        k = ""

                    k += re.sub(r"^config\.", "", key + ".")
                    if setting and len(setting) > 1:
                        setting = ".".join(setting.split(".")[1:])

                    values += self._get_setting(
                        value, k=k, setting=setting, default=default
                    )
                    k = orig_k

                    continue

                if isinstance(value, list):
                    value = ", ".join(
                        json.dumps(val) if isinstance(val, list) else val
                        for val in value
                    )
                    value = f"[{value}]"

                value = json.dumps(value)

                values.append(((k or "") + key, value))

            return values
