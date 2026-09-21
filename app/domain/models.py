"""Base estrita para modelos Pydantic do domínio e contratos."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Modelo com extra=forbid — propriedades extras são rejeitadas."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )
