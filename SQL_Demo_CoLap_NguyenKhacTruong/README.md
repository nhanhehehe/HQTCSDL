# Demo cac muc co lap SQL Server

## Thu tu chay

1. Chay `00_KhoiTao.sql` mot lan.
2. Mo hai cua so Query trong SSMS, cung dung database `IsolationLevelDemo_NguyenKhacTruong`.
3. Mo file T1 o cua so thu nhat va file T2 o cua so thu hai.
4. Chay T1 truoc; trong khoang 10 giay `WAITFOR`, chay T2.
5. Chay lai `00_KhoiTao.sql` truoc moi demo de dua du lieu ve ban dau.
   Hoac chay nhanh `06_ResetDuLieu.sql` de reset du lieu ma khong tao lai database.

## Cac demo

- `01_DirtyRead_T1.sql` va `01_DirtyRead_T2.sql`: Dirty Read.
- `01_DirtyRead_T1.sql` va `01_ReadCommitted_T2.sql`: khac phuc Dirty Read.
- `02_UnrepeatableRead_T1.sql` va `02_UnrepeatableRead_T2.sql`: Unrepeatable Read.
- `02_PhantomRead_T1.sql` va `02_PhantomRead_T2.sql`: Phantom Read.
- `02_LostUpdate_T1.sql` va `02_LostUpdate_T2.sql`: Lost Update.
- `03_RepeatableRead_T1.sql` va `03_RepeatableRead_T2.sql`: khac phuc Unrepeatable Read.
- `04_Serializable_T1.sql` va `04_Serializable_T2.sql`: khac phuc Phantom Read.
- `05_Updlock_T1.sql` va `05_Updlock_T2.sql`: khac phuc Lost Update.

Ten chu tai khoan trong du lieu mau la `NguyenKhacTruong`. Ma tai khoan `A001`, `A002`... duoc giu nguyen de lam khoa dinh danh va de de theo doi giao tac.
