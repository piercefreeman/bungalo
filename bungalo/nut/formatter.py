from pydantic import BaseModel

VALUE_TYPE = str | int | float | bool


class Command(BaseModel):
    values: list[VALUE_TYPE]

    def render(self) -> str:
        return " ".join(format_python_value(value) for value in self.values)


class Section(BaseModel):
    """
    Formatter for a nut config file section. These files use different syntax depending
    on what is being defined, but the general format is as follows:

    Format 1, dictionary assignment:

    ```
    [ups]
    driver = usbhid-ups
    port = auto
    desc = "Local UPS"
    pollinterval = 2
    ```

    Format 2, list assignment of commands:

    ```
    LISTEN 127.0.0.1 3493
    LISTEN ::1 3493
    MAXAGE 15
    ```

    """

    title: str | None = None
    list_values: list[VALUE_TYPE | Command] = []
    dict_values: dict[str, VALUE_TYPE | Command] = {}

    def render(self) -> str:
        payload = ""
        if self.title:
            payload += f"[{self.title}]\n"
        for key, value in self.dict_values.items():
            payload += f"{key} = {format_python_value(value)}\n"
        for value in self.list_values:
            payload += f"{format_python_value(value)}\n"
        return payload


def format_python_value(value: VALUE_TYPE | Command) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, float):
        return str(value)
    elif isinstance(value, str):
        words = value.split()
        if len(words) > 1:
            return f'"{value}"'
        else:
            return value
    elif isinstance(value, Command):
        return value.render()
    else:
        raise ValueError(f"Unsupported value type: {type(value)}")
