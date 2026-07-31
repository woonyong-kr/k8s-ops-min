"""Base Pydantic model shared by contracts outside the gateway package.

Keeping the model at the contracts root prevents domain-neutral contracts from
initialising the gateway package solely to inherit validation behaviour.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
