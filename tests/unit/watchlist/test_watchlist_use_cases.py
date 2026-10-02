"""
Unit tests for Watchlist use cases.
"""

import pytest
from typing import Dict, List, Optional, Union

from app.modules.watchlist.application.ports import (
    DuplicateStockError,
    DuplicateWatchlistError,
    InvalidWatchlistDataError,
    StockNotFoundError,
    WatchlistNotFoundError,
    WatchlistRepository,
)
from app.modules.watchlist.application.use_cases.add_stock_to_watchlist import (
    AddStockToWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.create_watchlist import (
    CreateWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.delete_watchlist import (
    DeleteWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.get_watchlist import (
    GetWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.get_watchlists import (
    GetWatchlistsUseCase,
)
from app.modules.watchlist.application.use_cases.remove_stock_from_watchlist import (
    RemoveStockFromWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.update_watchlist import (
    UpdateWatchlistUseCase,
)
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem


class InMemoryWatchlistRepository(WatchlistRepository):
    def __init__(self):
        self.watchlists: Dict[int, Watchlist] = {}
        self.items: Dict[int, List[WatchlistItem]] = {}
        self._next_wl_id = 1
        self._next_item_id = 1

    async def get_by_id(self, watchlist_id: int) -> Optional[Watchlist]:
        wl = self.watchlists.get(watchlist_id)
        if not wl:
            return None
        items = self.items.get(watchlist_id, [])
        return Watchlist(
            id=wl.id,
            user_id=wl.user_id,
            name=wl.name,
            items=list(items),
            created_at=wl.created_at,
            updated_at=wl.updated_at,
        )

    async def get_by_user_id(self, user_id: int) -> List[Watchlist]:
        res = []
        for wl in self.watchlists.values():
            if wl.user_id == user_id:
                items = self.items.get(wl.id, [])
                res.append(
                    Watchlist(
                        id=wl.id,
                        user_id=wl.user_id,
                        name=wl.name,
                        items=list(items),
                    )
                )
        return res

    async def get_by_user_and_name(self, user_id: int, name: str) -> Optional[Watchlist]:
        clean_name = (name or "").strip()
        for wl in self.watchlists.values():
            if wl.user_id == user_id and wl.name == clean_name:
                items = self.items.get(wl.id, [])
                return Watchlist(
                    id=wl.id,
                    user_id=wl.user_id,
                    name=wl.name,
                    items=list(items),
                    created_at=wl.created_at,
                    updated_at=wl.updated_at,
                )
        return None

    async def get_user_watchlists_summary(self, user_id: int) -> List[Watchlist]:
        res = []
        for wl in self.watchlists.values():
            if wl.user_id == user_id:
                items = self.items.get(wl.id, [])
                res.append(
                    Watchlist(
                        id=wl.id,
                        user_id=wl.user_id,
                        name=wl.name,
                        items=[],
                        _stock_count=len(items),
                        created_at=wl.created_at,
                        updated_at=wl.updated_at,
                    )
                )
        return res

    async def save_watchlist(self, watchlist: Watchlist) -> Watchlist:
        if watchlist.id is None:
            wl_id = self._next_wl_id
            self._next_wl_id += 1
            saved = Watchlist(id=wl_id, user_id=watchlist.user_id, name=watchlist.name)
            self.watchlists[wl_id] = saved
            self.items[wl_id] = []
            return saved
        else:
            self.watchlists[watchlist.id].name = watchlist.name
            return self.watchlists[watchlist.id]

    async def delete_watchlist(self, watchlist_id: int) -> bool:
        if watchlist_id in self.watchlists:
            del self.watchlists[watchlist_id]
            self.items.pop(watchlist_id, None)
            return True
        return False

    async def add_item(self, watchlist_id: int, item: WatchlistItem) -> WatchlistItem:
        item_id = self._next_item_id
        self._next_item_id += 1
        saved = WatchlistItem(
            id=item_id,
            watchlist_id=watchlist_id,
            symbol=item.symbol,
            exchange=item.exchange,
            notes=item.notes,
        )
        if watchlist_id not in self.items:
            self.items[watchlist_id] = []
        self.items[watchlist_id].append(saved)
        return saved

    async def remove_item(self, watchlist_id: int, symbol_or_item_id: Union[str, int]) -> bool:
        items = self.items.get(watchlist_id, [])
        for i, it in enumerate(items):
            if isinstance(symbol_or_item_id, int) and it.id == symbol_or_item_id:
                items.pop(i)
                return True
            elif isinstance(symbol_or_item_id, str) and (
                (symbol_or_item_id.isdigit() and it.id == int(symbol_or_item_id))
                or it.symbol == symbol_or_item_id.upper()
            ):
                items.pop(i)
                return True
        return False

    async def get_item_by_symbol(self, watchlist_id: int, symbol: str) -> Optional[WatchlistItem]:
        items = self.items.get(watchlist_id, [])
        for it in items:
            if it.symbol == symbol.upper():
                return it
        return None


@pytest.fixture
def repo():
    return InMemoryWatchlistRepository()


@pytest.mark.asyncio
async def test_create_watchlist_success(repo):
    use_case = CreateWatchlistUseCase(repo)
    wl = await use_case.execute(user_id=1, name="Bluechip Stocks")
    assert wl.id is not None
    assert wl.user_id == 1
    assert wl.name == "Bluechip Stocks"


@pytest.mark.asyncio
async def test_create_watchlist_empty_name_fails(repo):
    use_case = CreateWatchlistUseCase(repo)
    with pytest.raises(InvalidWatchlistDataError):
        await use_case.execute(user_id=1, name="   ")


@pytest.mark.asyncio
async def test_get_watchlists_for_user(repo):
    create_uc = CreateWatchlistUseCase(repo)
    get_all_uc = GetWatchlistsUseCase(repo)

    await create_uc.execute(user_id=1, name="IT")
    await create_uc.execute(user_id=1, name="Pharma")
    await create_uc.execute(user_id=2, name="Banking")

    user1_watchlists = await get_all_uc.execute(user_id=1)
    assert len(user1_watchlists) == 2
    assert {w.name for w in user1_watchlists} == {"IT", "Pharma"}

    user2_watchlists = await get_all_uc.execute(user_id=2)
    assert len(user2_watchlists) == 1
    assert user2_watchlists[0].name == "Banking"


@pytest.mark.asyncio
async def test_get_single_watchlist_and_ownership(repo):
    create_uc = CreateWatchlistUseCase(repo)
    get_uc = GetWatchlistUseCase(repo)

    wl = await create_uc.execute(user_id=10, name="Growth")

    # Correct owner
    found = await get_uc.execute(user_id=10, watchlist_id=wl.id)
    assert found.name == "Growth"

    # Unauthorized user
    with pytest.raises(WatchlistNotFoundError):
        await get_uc.execute(user_id=99, watchlist_id=wl.id)

    # Non-existent ID
    with pytest.raises(WatchlistNotFoundError):
        await get_uc.execute(user_id=10, watchlist_id=9999)


@pytest.mark.asyncio
async def test_update_watchlist(repo):
    create_uc = CreateWatchlistUseCase(repo)
    update_uc = UpdateWatchlistUseCase(repo)

    wl = await create_uc.execute(user_id=5, name="Old Name")
    updated = await update_uc.execute(user_id=5, watchlist_id=wl.id, name="New Name")
    assert updated.name == "New Name"

    # User cannot update another's watchlist
    with pytest.raises(WatchlistNotFoundError):
        await update_uc.execute(user_id=99, watchlist_id=wl.id, name="Hacked Name")

    # Empty name fails
    with pytest.raises(InvalidWatchlistDataError):
        await update_uc.execute(user_id=5, watchlist_id=wl.id, name=" ")


@pytest.mark.asyncio
async def test_delete_watchlist(repo):
    create_uc = CreateWatchlistUseCase(repo)
    delete_uc = DeleteWatchlistUseCase(repo)
    get_uc = GetWatchlistUseCase(repo)

    wl = await create_uc.execute(user_id=7, name="To Delete")
    success = await delete_uc.execute(user_id=7, watchlist_id=wl.id)
    assert success is True

    with pytest.raises(WatchlistNotFoundError):
        await get_uc.execute(user_id=7, watchlist_id=wl.id)

    # Deleting another user's watchlist fails
    wl2 = await create_uc.execute(user_id=7, name="Safe")
    with pytest.raises(WatchlistNotFoundError):
        await delete_uc.execute(user_id=88, watchlist_id=wl2.id)


@pytest.mark.asyncio
async def test_add_and_remove_stock_with_duplicate_prevention(repo):
    create_uc = CreateWatchlistUseCase(repo)
    add_uc = AddStockToWatchlistUseCase(repo)
    remove_uc = RemoveStockFromWatchlistUseCase(repo)
    get_uc = GetWatchlistUseCase(repo)

    wl = await create_uc.execute(user_id=1, name="IT Equities")

    # Add TCS
    item1 = await add_uc.execute(user_id=1, watchlist_id=wl.id, symbol="TCS", exchange="NSE", notes="Leader")
    assert item1.symbol == "TCS"
    assert item1.exchange == "NSE"

    # Add INFY
    item2 = await add_uc.execute(user_id=1, watchlist_id=wl.id, symbol="infy")
    assert item2.symbol == "INFY"

    # Duplicate addition of TCS should raise DuplicateStockError
    with pytest.raises(DuplicateStockError):
        await add_uc.execute(user_id=1, watchlist_id=wl.id, symbol="tcs")

    # Unauthorized user cannot add stock
    with pytest.raises(WatchlistNotFoundError):
        await add_uc.execute(user_id=2, watchlist_id=wl.id, symbol="WIPRO")

    # Check watchlist contents
    current = await get_uc.execute(user_id=1, watchlist_id=wl.id)
    assert current.stock_count == 2
    assert [i.symbol for i in current.items] == ["TCS", "INFY"]

    # Remove INFY by symbol
    await remove_uc.execute(user_id=1, watchlist_id=wl.id, symbol_or_item_id="INFY")
    current_after = await get_uc.execute(user_id=1, watchlist_id=wl.id)
    assert current_after.stock_count == 1
    assert current_after.items[0].symbol == "TCS"

    # Remove non-existent stock raises StockNotFoundError
    with pytest.raises(StockNotFoundError):
        await remove_uc.execute(user_id=1, watchlist_id=wl.id, symbol_or_item_id="RELIANCE")

    # Remove TCS by item id
    await remove_uc.execute(user_id=1, watchlist_id=wl.id, symbol_or_item_id=item1.id)
    empty_wl = await get_uc.execute(user_id=1, watchlist_id=wl.id)
    assert empty_wl.stock_count == 0


@pytest.mark.asyncio
async def test_get_watchlists_summary_use_case(repo):
    create_uc = CreateWatchlistUseCase(repo)
    add_uc = AddStockToWatchlistUseCase(repo)
    get_all_uc = GetWatchlistsUseCase(repo)

    wl1 = await create_uc.execute(user_id=1, name="IT")
    wl2 = await create_uc.execute(user_id=1, name="Banking")
    await add_uc.execute(user_id=1, watchlist_id=wl1.id, symbol="TCS")
    await add_uc.execute(user_id=1, watchlist_id=wl1.id, symbol="INFY")

    # summary=True returns summaries without loaded items but with correct stock_count
    summaries = await get_all_uc.execute(user_id=1, summary=True)
    assert len(summaries) == 2
    it_summary = next(w for w in summaries if w.id == wl1.id)
    banking_summary = next(w for w in summaries if w.id == wl2.id)

    assert it_summary.stock_count == 2
    assert it_summary.items == []  # items not loaded
    assert banking_summary.stock_count == 0
    assert banking_summary.items == []



# ---------------------------------------------------------------------------
# Duplicate watchlist name prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_watchlist_name_same_user_raises(repo):
    """Creating two watchlists with the same name for the same user raises DuplicateWatchlistError."""
    use_case = CreateWatchlistUseCase(repo)

    await use_case.execute(user_id=9, name="Bankings")

    with pytest.raises(DuplicateWatchlistError):
        await use_case.execute(user_id=9, name="Bankings")


@pytest.mark.asyncio
async def test_create_same_name_different_users_succeeds(repo):
    """Two different users can each have a watchlist with the same name."""
    use_case = CreateWatchlistUseCase(repo)

    wl_user1 = await use_case.execute(user_id=1, name="Tech")
    wl_user2 = await use_case.execute(user_id=2, name="Tech")

    assert wl_user1.id != wl_user2.id
    assert wl_user1.name == wl_user2.name == "Tech"


@pytest.mark.asyncio
async def test_update_watchlist_to_duplicate_name_raises(repo):
    """Renaming a watchlist to a name already taken by the same user raises DuplicateWatchlistError."""
    create_uc = CreateWatchlistUseCase(repo)
    update_uc = UpdateWatchlistUseCase(repo)

    await create_uc.execute(user_id=3, name="Alpha")
    wl_beta = await create_uc.execute(user_id=3, name="Beta")

    with pytest.raises(DuplicateWatchlistError):
        await update_uc.execute(user_id=3, watchlist_id=wl_beta.id, name="Alpha")


@pytest.mark.asyncio
async def test_update_watchlist_same_name_no_error(repo):
    """Updating a watchlist to its own current name should succeed without raising DuplicateWatchlistError."""
    create_uc = CreateWatchlistUseCase(repo)
    update_uc = UpdateWatchlistUseCase(repo)

    wl = await create_uc.execute(user_id=4, name="Stable")
    updated = await update_uc.execute(user_id=4, watchlist_id=wl.id, name="Stable")
    assert updated.name == "Stable"
