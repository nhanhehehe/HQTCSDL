/* Dua du lieu ve trang thai ban dau cho cac bai demo */
USE [IsolationLevelDemo_NguyenKhacTruong];
GO

-- Xoa cac tai khoan phat sinh tu demo Phantom/Serializable
DELETE FROM dbo.TaiKhoan
WHERE MaTK NOT IN ('A001', 'A002', 'A003');

-- Khoi phuc gia tri ban dau
UPDATE dbo.TaiKhoan
SET TenTK = N'NguyenKhacTruong',
    SoTien = CASE MaTK
                WHEN 'A001' THEN 100000
                WHEN 'A002' THEN 200000
                WHEN 'A003' THEN 50000
             END
WHERE MaTK IN ('A001', 'A002', 'A003');

-- Neu tai khoan mau bi xoa thi tao lai
IF NOT EXISTS (SELECT 1 FROM dbo.TaiKhoan WHERE MaTK = 'A001')
    INSERT INTO dbo.TaiKhoan (MaTK, TenTK, SoTien)
    VALUES ('A001', N'NguyenKhacTruong', 100000);

IF NOT EXISTS (SELECT 1 FROM dbo.TaiKhoan WHERE MaTK = 'A002')
    INSERT INTO dbo.TaiKhoan (MaTK, TenTK, SoTien)
    VALUES ('A002', N'NguyenKhacTruong', 200000);

IF NOT EXISTS (SELECT 1 FROM dbo.TaiKhoan WHERE MaTK = 'A003')
    INSERT INTO dbo.TaiKhoan (MaTK, TenTK, SoTien)
    VALUES ('A003', N'NguyenKhacTruong', 50000);
GO

SELECT * FROM dbo.TaiKhoan ORDER BY MaTK;
GO
