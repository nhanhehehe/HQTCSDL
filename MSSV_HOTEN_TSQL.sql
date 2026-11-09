/*
    Ho ten: <DIEN_HO_TEN>
    MSSV: <DIEN_MSSV>
    Bai tap: T-SQL - Quan ly nhan su

    Huong dan:
    1. Doi ten file thanh: MSSV_HOTEN_TSQL.sql
    2. Neu chay lan dau, chay tu dau den het phan du lieu mau de tao CSDL va du lieu.
    3. Chay tung cau tu Cau 1 den Cau 10 theo thu tu.
*/

/* =========================================================
   1. TAO CO SO DU LIEU
   ========================================================= */

USE master;
GO

IF DB_ID('HRM_DB') IS NOT NULL
BEGIN
    ALTER DATABASE HRM_DB SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE HRM_DB;
END
GO

CREATE DATABASE HRM_DB;
GO

USE HRM_DB;
GO

/* =========================================================
   2. TAO 10 BANG
   ========================================================= */

CREATE TABLE PHONGBAN
(
    MaPB CHAR(5) PRIMARY KEY,
    TenPB NVARCHAR(100) NOT NULL,
    DiaDiem NVARCHAR(200),
    NgayThanhLap DATE,
    SDT VARCHAR(15)
);
GO

CREATE TABLE CHUCVU
(
    MaCV CHAR(5) PRIMARY KEY,
    TenCV NVARCHAR(100) NOT NULL,
    PhuCap DECIMAL(15,2) DEFAULT 0
);
GO

CREATE TABLE NHANVIEN
(
    MaNV CHAR(6) PRIMARY KEY,
    HoTen NVARCHAR(100) NOT NULL,
    NgaySinh DATE NOT NULL,
    GioiTinh NVARCHAR(5) CHECK (GioiTinh IN (N'Nam', N'Nữ')),
    DiaChi NVARCHAR(200),
    SDT VARCHAR(15),
    Email VARCHAR(100),
    NgayVaoLam DATE NOT NULL,
    LuongCoBan DECIMAL(15,2) NOT NULL CHECK (LuongCoBan > 0),
    MaPB CHAR(5) NOT NULL,
    MaCV CHAR(5) NOT NULL,
    CONSTRAINT FK_NV_PB FOREIGN KEY (MaPB) REFERENCES PHONGBAN(MaPB),
    CONSTRAINT FK_NV_CV FOREIGN KEY (MaCV) REFERENCES CHUCVU(MaCV)
);
GO

CREATE TABLE HOPDONGLAODONG
(
    MaHD CHAR(7) PRIMARY KEY,
    MaNV CHAR(6) NOT NULL,
    LoaiHD NVARCHAR(50) NOT NULL,
    NgayBatDau DATE NOT NULL,
    NgayKetThuc DATE,
    HeSoLuong DECIMAL(5,2) DEFAULT 1.0,
    TrangThai NVARCHAR(30) DEFAULT N'Hiệu lực',
    CONSTRAINT FK_HD_NV FOREIGN KEY (MaNV) REFERENCES NHANVIEN(MaNV),
    CONSTRAINT CK_HD_NGAY CHECK (NgayKetThuc IS NULL OR NgayKetThuc >= NgayBatDau)
);
GO

CREATE TABLE DUAN
(
    MaDA CHAR(6) PRIMARY KEY,
    TenDA NVARCHAR(150) NOT NULL,
    NgayBatDau DATE,
    NgayKetThuc DATE,
    KinhPhi DECIMAL(18,2) CHECK (KinhPhi >= 0),
    MaPB CHAR(5) NOT NULL,
    CONSTRAINT FK_DA_PB FOREIGN KEY (MaPB) REFERENCES PHONGBAN(MaPB),
    CONSTRAINT CK_DA_NGAY CHECK (NgayKetThuc IS NULL OR NgayKetThuc >= NgayBatDau)
);
GO

CREATE TABLE PHANCONG
(
    MaNV CHAR(6),
    MaDA CHAR(6),
    NgayThamGia DATE NOT NULL,
    SoGio INT DEFAULT 0,
    VaiTro NVARCHAR(100),
    CONSTRAINT PK_PHANCONG PRIMARY KEY (MaNV, MaDA),
    CONSTRAINT FK_PC_NV FOREIGN KEY (MaNV) REFERENCES NHANVIEN(MaNV),
    CONSTRAINT FK_PC_DA FOREIGN KEY (MaDA) REFERENCES DUAN(MaDA),
    CONSTRAINT CK_PC_SOGIO CHECK (SoGio >= 0)
);
GO

CREATE TABLE CHAMCONG
(
    MaCC INT IDENTITY(1,1) PRIMARY KEY,
    MaNV CHAR(6) NOT NULL,
    Ngay DATE NOT NULL,
    GioVao TIME,
    GioRa TIME,
    TrangThai NVARCHAR(30) DEFAULT N'Đi làm',
    CONSTRAINT FK_CC_NV FOREIGN KEY (MaNV) REFERENCES NHANVIEN(MaNV),
    CONSTRAINT UQ_CC_NV_NGAY UNIQUE (MaNV, Ngay)
);
GO

CREATE TABLE KHOADAOTAO
(
    MaKhoa CHAR(6) PRIMARY KEY,
    TenKhoa NVARCHAR(150) NOT NULL,
    NgayBatDau DATE,
    NgayKetThuc DATE,
    KinhPhi DECIMAL(15,2) DEFAULT 0,
    CONSTRAINT CK_KDT_NGAY CHECK (NgayKetThuc IS NULL OR NgayKetThuc >= NgayBatDau)
);
GO

CREATE TABLE NHANVIEN_DAOTAO
(
    MaNV CHAR(6),
    MaKhoa CHAR(6),
    KetQua DECIMAL(4,2),
    XepLoai NVARCHAR(30),
    CONSTRAINT PK_NV_DT PRIMARY KEY (MaNV, MaKhoa),
    CONSTRAINT FK_NVDT_NV FOREIGN KEY (MaNV) REFERENCES NHANVIEN(MaNV),
    CONSTRAINT FK_NVDT_KHOA FOREIGN KEY (MaKhoa) REFERENCES KHOADAOTAO(MaKhoa),
    CONSTRAINT CK_NVDT_KQ CHECK (KetQua IS NULL OR KetQua BETWEEN 0 AND 10)
);
GO

CREATE TABLE BANGLUONG
(
    MaLuong INT IDENTITY(1,1) PRIMARY KEY,
    MaNV CHAR(6) NOT NULL,
    Thang INT NOT NULL CHECK (Thang BETWEEN 1 AND 12),
    Nam INT NOT NULL CHECK (Nam >= 2000),
    LuongCoBan DECIMAL(15,2) NOT NULL,
    PhuCap DECIMAL(15,2) DEFAULT 0,
    Thuong DECIMAL(15,2) DEFAULT 0,
    KhauTru DECIMAL(15,2) DEFAULT 0,
    CONSTRAINT FK_BL_NV FOREIGN KEY (MaNV) REFERENCES NHANVIEN(MaNV),
    CONSTRAINT UQ_BL_NV_THANG_NAM UNIQUE (MaNV, Thang, Nam)
);
GO

/* =========================================================
   3. DU LIEU MAU
   ========================================================= */

INSERT INTO PHONGBAN
VALUES
('PB001', N'Phòng Công nghệ thông tin', N'Tầng 3', '2018-01-01', '0281111111'),
('PB002', N'Phòng Nhân sự', N'Tầng 2', '2017-05-10', '0282222222'),
('PB003', N'Phòng Kế toán', N'Tầng 2', '2019-03-15', '0283333333'),
('PB004', N'Phòng Kinh doanh', N'Tầng 4', '2020-06-01', '0284444444');
GO

INSERT INTO CHUCVU
VALUES
('CV001', N'Nhân viên', 500000),
('CV002', N'Trưởng nhóm', 1500000),
('CV003', N'Trưởng phòng', 3000000),
('CV004', N'Giám đốc', 5000000);
GO

INSERT INTO NHANVIEN
(
    MaNV, HoTen, NgaySinh, GioiTinh, DiaChi, SDT, Email,
    NgayVaoLam, LuongCoBan, MaPB, MaCV
)
VALUES
('NV0001', N'Nguyễn Văn An', '1995-05-12', N'Nam', N'TP.HCM', '0901000001', 'an@company.com', '2020-01-10', 12000000, 'PB001', 'CV001'),
('NV0002', N'Trần Thị Bình', '1992-08-20', N'Nữ', N'TP.HCM', '0901000002', 'binh@company.com', '2019-03-15', 15000000, 'PB001', 'CV002'),
('NV0003', N'Lê Hoàng Cường', '1985-03-11', N'Nam', N'Đồng Nai', '0901000003', 'cuong@company.com', '2017-06-20', 20000000, 'PB001', 'CV003'),
('NV0004', N'Phạm Thị Dung', '1998-11-02', N'Nữ', N'Bình Dương', '0901000004', 'dung@company.com', '2022-05-01', 10000000, 'PB002', 'CV001'),
('NV0005', N'Hoàng Minh Đức', '1990-02-14', N'Nam', N'TP.HCM', '0901000005', 'duc@company.com', '2018-09-10', 18000000, 'PB003', 'CV002'),
('NV0006', N'Võ Thị Hạnh', '1988-07-07', N'Nữ', N'TP.HCM', '0901000006', 'hanh@company.com', '2016-10-10', 22000000, 'PB002', 'CV003'),
('NV0007', N'Đặng Quốc Khánh', '2000-09-17', N'Nam', N'Long An', '0901000007', 'khanh@company.com', '2023-02-01', 9000000, 'PB004', 'CV001'),
('NV0008', N'Nguyễn Ngọc Lan', '1997-12-25', N'Nữ', N'TP.HCM', '0901000008', 'lan@company.com', '2021-07-15', 11000000, 'PB004', 'CV001');
GO

INSERT INTO HOPDONGLAODONG
VALUES
('HD00001', 'NV0001', N'Không xác định thời hạn', '2020-01-10', NULL, 1.5, N'Hiệu lực'),
('HD00002', 'NV0002', N'Không xác định thời hạn', '2019-03-15', NULL, 1.8, N'Hiệu lực'),
('HD00003', 'NV0003', N'Không xác định thời hạn', '2017-06-20', NULL, 2.5, N'Hiệu lực'),
('HD00004', 'NV0004', N'3 năm', '2022-05-01', '2027-05-01', 1.3, N'Hiệu lực'),
('HD00005', 'NV0005', N'Không xác định thời hạn', '2018-09-10', NULL, 2.0, N'Hiệu lực');
GO

INSERT INTO DUAN
VALUES
('DA0001', N'Hệ thống ERP', '2026-01-01', '2026-12-31', 1000000000, 'PB001'),
('DA0002', N'Hệ thống AI tuyển dụng', '2026-03-01', '2026-10-30', 700000000, 'PB002'),
('DA0003', N'Hệ thống bán hàng', '2026-02-01', '2026-08-31', 500000000, 'PB004');
GO

INSERT INTO PHANCONG
VALUES
('NV0001','DA0001','2026-01-01',200,N'Lập trình viên'),
('NV0002','DA0001','2026-01-01',150,N'Trưởng nhóm'),
('NV0003','DA0001','2026-01-01',100,N'Quản lý'),
('NV0004','DA0002','2026-03-01',180,N'Phân tích nghiệp vụ'),
('NV0006','DA0002','2026-03-01',80,N'Quản lý'),
('NV0007','DA0003','2026-02-01',160,N'Nhân viên kinh doanh'),
('NV0008','DA0003','2026-02-01',170,N'Nhân viên kinh doanh');
GO

INSERT INTO KHOADAOTAO
VALUES
('KH0001', N'T-SQL nâng cao', '2026-04-01', '2026-04-15', 5000000),
('KH0002', N'Kỹ năng quản lý', '2026-05-01', '2026-05-20', 8000000),
('KH0003', N'Ứng dụng AI trong doanh nghiệp', '2026-06-01', '2026-06-15', 10000000);
GO

INSERT INTO NHANVIEN_DAOTAO
VALUES
('NV0001','KH0001',8.5,NULL),
('NV0002','KH0001',7.5,NULL),
('NV0003','KH0002',9.0,NULL),
('NV0004','KH0003',6.5,NULL),
('NV0007','KH0003',4.5,NULL);
GO

INSERT INTO BANGLUONG
(
    MaNV, Thang, Nam, LuongCoBan, PhuCap, Thuong, KhauTru
)
VALUES
('NV0001',9,2026,12000000, 500000,1000000,200000),
('NV0002',9,2026,15000000,1500000,2000000,300000),
('NV0003',9,2026,20000000,3000000,3000000,500000),
('NV0004',9,2026,10000000, 500000, 500000,100000),
('NV0005',9,2026,18000000,1500000,1000000,200000),
('NV0006',9,2026,22000000,3000000,2500000,400000),
('NV0007',9,2026, 9000000, 500000, 300000,100000),
('NV0008',9,2026,11000000, 500000, 700000,150000);
GO

/* =========================================================
   CAU 1 - BIEN VA TOAN TU
   ========================================================= */

DECLARE @LuongCoBan DECIMAL(15,2);
DECLARE @PhuCap DECIMAL(15,2);
DECLARE @Thuong DECIMAL(15,2);
DECLARE @KhauTru DECIMAL(15,2);
DECLARE @ThucLinh DECIMAL(15,2);

SELECT
    @LuongCoBan = LuongCoBan,
    @PhuCap = PhuCap,
    @Thuong = Thuong,
    @KhauTru = KhauTru
FROM BANGLUONG
WHERE MaNV = 'NV0001' AND Thang = 9 AND Nam = 2026;

SET @ThucLinh = @LuongCoBan + @PhuCap + @Thuong - @KhauTru;

PRINT N'Lương thực lĩnh của NV0001: ' + CAST(@ThucLinh AS NVARCHAR(30));
GO

/* =========================================================
   CAU 2 - BIEN KET HOP TRUY VAN
   ========================================================= */

DECLARE @MaNV CHAR(6) = 'NV0002';
DECLARE @HoTen NVARCHAR(100);
DECLARE @LuongCB DECIMAL(15,2);
DECLARE @TenPB NVARCHAR(100);

SELECT
    @HoTen = NV.HoTen,
    @LuongCB = NV.LuongCoBan,
    @TenPB = PB.TenPB
FROM NHANVIEN AS NV
INNER JOIN PHONGBAN AS PB ON NV.MaPB = PB.MaPB
WHERE NV.MaNV = @MaNV;

PRINT N'Nhân viên: ' + @HoTen;
PRINT N'Phòng ban: ' + @TenPB;
PRINT N'Lương: ' + CAST(@LuongCB AS NVARCHAR(30));
GO

/* =========================================================
   CAU 3 - IF ELSE
   ========================================================= */

DECLARE @MaNV CHAR(6) = 'NV0007';
DECLARE @HoTen NVARCHAR(100);
DECLARE @LuongCoBan DECIMAL(15,2);
DECLARE @PhanLoai NVARCHAR(50);

SELECT
    @HoTen = HoTen,
    @LuongCoBan = LuongCoBan
FROM NHANVIEN
WHERE MaNV = @MaNV;

IF @LuongCoBan < 10000000
    SET @PhanLoai = N'Thu nhập thấp';
ELSE IF @LuongCoBan < 20000000
    SET @PhanLoai = N'Thu nhập trung bình';
ELSE
    SET @PhanLoai = N'Thu nhập cao';

PRINT N'Nhân viên: ' + @HoTen;
PRINT N'Lương: ' + CAST(@LuongCoBan AS NVARCHAR(30));
PRINT N'Phân loại: ' + @PhanLoai;
GO

/* =========================================================
   CAU 4 - IF EXISTS
   ========================================================= */

DECLARE @MaNV CHAR(6) = 'NV0001';
DECLARE @MaDA CHAR(6) = 'DA0003';

IF NOT EXISTS (SELECT 1 FROM NHANVIEN WHERE MaNV = @MaNV)
BEGIN
    PRINT N'Nhân viên không tồn tại';
END
ELSE IF NOT EXISTS (SELECT 1 FROM DUAN WHERE MaDA = @MaDA)
BEGIN
    PRINT N'Dự án không tồn tại';
END
ELSE IF EXISTS (SELECT 1 FROM PHANCONG WHERE MaNV = @MaNV AND MaDA = @MaDA)
BEGIN
    PRINT N'Nhân viên đã tham gia dự án';
END
ELSE
BEGIN
    INSERT INTO PHANCONG (MaNV, MaDA, NgayThamGia, SoGio, VaiTro)
    VALUES (@MaNV, @MaDA, GETDATE(), 0, N'Nhân viên');

    PRINT N'Phân công thành công';
END
GO

/* =========================================================
   CAU 5 - CASE
   ========================================================= */

SELECT
    MaNV,
    HoTen,
    NgaySinh,
    DATEDIFF(YEAR, NgaySinh, GETDATE()) AS Tuoi,
    LuongCoBan,
    CASE
        WHEN LuongCoBan < 10000000 THEN N'Thấp'
        WHEN LuongCoBan < 15000000 THEN N'Trung bình'
        WHEN LuongCoBan < 20000000 THEN N'Khá'
        ELSE N'Cao'
    END AS XepLoaiLuong
FROM NHANVIEN;
GO

/* =========================================================
   CAU 6 - CASE KET HOP JOIN VA UPDATE
   ========================================================= */

-- Phan A
SELECT
    NV.MaNV,
    NV.HoTen,
    KDT.TenKhoa,
    NVDT.KetQua,
    CASE
        WHEN NVDT.KetQua < 5 THEN N'Không đạt'
        WHEN NVDT.KetQua < 6.5 THEN N'Trung bình'
        WHEN NVDT.KetQua < 8 THEN N'Khá'
        WHEN NVDT.KetQua < 9 THEN N'Giỏi'
        ELSE N'Xuất sắc'
    END AS XepLoai
FROM NHANVIEN_DAOTAO AS NVDT
INNER JOIN NHANVIEN AS NV ON NVDT.MaNV = NV.MaNV
INNER JOIN KHOADAOTAO AS KDT ON NVDT.MaKhoa = KDT.MaKhoa;
GO

-- Phan B
UPDATE NHANVIEN_DAOTAO
SET XepLoai =
    CASE
        WHEN KetQua < 5 THEN N'Không đạt'
        WHEN KetQua < 6.5 THEN N'Trung bình'
        WHEN KetQua < 8 THEN N'Khá'
        WHEN KetQua < 9 THEN N'Giỏi'
        ELSE N'Xuất sắc'
    END;
GO

SELECT
    NV.MaNV,
    NV.HoTen,
    KDT.TenKhoa,
    NVDT.KetQua,
    NVDT.XepLoai
FROM NHANVIEN_DAOTAO AS NVDT
INNER JOIN NHANVIEN AS NV ON NVDT.MaNV = NV.MaNV
INNER JOIN KHOADAOTAO AS KDT ON NVDT.MaKhoa = KDT.MaKhoa;
GO

/* =========================================================
   CAU 7 - WHILE, IF VA BREAK
   ========================================================= */

BEGIN TRAN;

DECLARE @LanTang INT = 0;
DECLARE @LuongTrungBinh DECIMAL(15,2);
DECLARE @LuongCaoNhat DECIMAL(15,2);

SELECT
    @LuongTrungBinh = AVG(LuongCoBan),
    @LuongCaoNhat = MAX(LuongCoBan)
FROM NHANVIEN;

WHILE @LuongTrungBinh < 18000000
BEGIN
    UPDATE NHANVIEN
    SET LuongCoBan = LuongCoBan * 1.05;

    SET @LanTang = @LanTang + 1;

    SELECT
        @LuongTrungBinh = AVG(LuongCoBan),
        @LuongCaoNhat = MAX(LuongCoBan)
    FROM NHANVIEN;

    PRINT N'Lần tăng: ' + CAST(@LanTang AS NVARCHAR(10));
    PRINT N'Lương trung bình: ' + CAST(@LuongTrungBinh AS NVARCHAR(30));
    PRINT N'Lương cao nhất: ' + CAST(@LuongCaoNhat AS NVARCHAR(30));

    IF @LuongCaoNhat > 40000000
        BREAK;
END

ROLLBACK TRAN;
GO

/* =========================================================
   CAU 8 - TRANSACTION VA @@ROWCOUNT
   ========================================================= */

DECLARE @MaNV CHAR(6) = 'NV0001';
DECLARE @MaPBMoi CHAR(5) = 'PB004';
DECLARE @SoDong INT;

BEGIN TRAN;

IF NOT EXISTS (SELECT 1 FROM NHANVIEN WHERE MaNV = @MaNV)
BEGIN
    PRINT N'Nhân viên không tồn tại';
    ROLLBACK TRAN;
END
ELSE IF NOT EXISTS (SELECT 1 FROM PHONGBAN WHERE MaPB = @MaPBMoi)
BEGIN
    PRINT N'Phòng ban không tồn tại';
    ROLLBACK TRAN;
END
ELSE
BEGIN
    UPDATE NHANVIEN
    SET MaPB = @MaPBMoi
    WHERE MaNV = @MaNV;

    SET @SoDong = @@ROWCOUNT;

    IF @SoDong = 1
    BEGIN
        COMMIT TRAN;
        PRINT N'Chuyển phòng thành công';
    END
    ELSE
    BEGIN
        ROLLBACK TRAN;
        PRINT N'Chuyển phòng thất bại - Transaction đã Rollback';
    END
END
GO

/* =========================================================
   CAU 9 - TRANSACTION XU LY NHIEU BANG
   ========================================================= */

BEGIN TRAN;

INSERT INTO NHANVIEN
(
    MaNV, HoTen, NgaySinh, GioiTinh, DiaChi, SDT, Email,
    NgayVaoLam, LuongCoBan, MaPB, MaCV
)
VALUES
(
    'NV0009',
    N'Nguyễn Minh Long',
    '1999-05-20',
    N'Nam',
    N'TP.HCM',
    '0901000009',
    'long@company.com',
    GETDATE(),
    11000000,
    'PB001',
    'CV001'
);

IF @@ERROR <> 0 OR @@ROWCOUNT <> 1
BEGIN
    ROLLBACK TRAN;
    PRINT N'Thêm nhân viên thất bại - Transaction đã Rollback';
END
ELSE
BEGIN
    INSERT INTO HOPDONGLAODONG
    (
        MaHD, MaNV, LoaiHD, NgayBatDau, NgayKetThuc,
        HeSoLuong, TrangThai
    )
    VALUES
    (
        'HD00009',
        'NV0009',
        N'3 năm',
        GETDATE(),
        DATEADD(YEAR, 3, GETDATE()),
        1.4,
        N'Hiệu lực'
    );

    IF @@ERROR <> 0 OR @@ROWCOUNT <> 1
    BEGIN
        ROLLBACK TRAN;
        PRINT N'Thêm hợp đồng thất bại - Transaction đã Rollback';
    END
    ELSE
    BEGIN
        COMMIT TRAN;
        PRINT N'Thêm nhân viên và hợp đồng thành công';
    END
END
GO

SELECT *
FROM NHANVIEN
WHERE MaNV = 'NV0009';

SELECT *
FROM HOPDONGLAODONG
WHERE MaNV = 'NV0009';
GO

/*
    Cau hoi bo sung Cau 9:
    Khong nen thuc hien hai lenh INSERT duoi dang hai giao dich doc lap
    vi nghiep vu them nhan vien va tao hop dong phai co tinh nguyen tu.
    Neu INSERT NHANVIEN thanh cong nhung INSERT HOPDONGLAODONG that bai,
    he thong se co nhan vien moi nhung khong co hop dong lao dong tuong ung.
    Dieu nay lam du lieu khong nhat quan. Dat ca hai lenh trong cung mot
    Transaction giup dam bao hoac ca hai cung thanh cong, hoac ca hai cung
    bi huy bo bang ROLLBACK.
*/

/* =========================================================
   CAU 10 - CURSOR TONG HOP THEO PHONG BAN
   ========================================================= */

DECLARE @MaPB CHAR(5);
DECLARE @TenPB NVARCHAR(100);
DECLARE @SoNhanVien INT;
DECLARE @TongLuong DECIMAL(18,2);
DECLARE @LuongTrungBinh DECIMAL(18,2);

DECLARE @MaNV CHAR(6);
DECLARE @HoTen NVARCHAR(100);
DECLARE @LuongCoBan DECIMAL(15,2);
DECLARE @XepLoaiLuong NVARCHAR(30);

DECLARE CursorPhongBan CURSOR FOR
SELECT MaPB, TenPB
FROM PHONGBAN
ORDER BY MaPB;

OPEN CursorPhongBan;

FETCH NEXT FROM CursorPhongBan INTO @MaPB, @TenPB;

WHILE @@FETCH_STATUS = 0
BEGIN
    SELECT
        @SoNhanVien = COUNT(NV.MaNV),
        @TongLuong = ISNULL(SUM(NV.LuongCoBan), 0),
        @LuongTrungBinh = ISNULL(AVG(NV.LuongCoBan), 0)
    FROM NHANVIEN AS NV
    WHERE NV.MaPB = @MaPB;

    PRINT N'=====================================';
    PRINT N'Phòng: ' + @TenPB;
    PRINT N'Số nhân viên: ' + CAST(@SoNhanVien AS NVARCHAR(10));
    PRINT N'Tổng lương: ' + CAST(@TongLuong AS NVARCHAR(30));
    PRINT N'Lương trung bình: ' + CAST(@LuongTrungBinh AS NVARCHAR(30));
    PRINT N'Danh sách:';

    DECLARE CursorNhanVien CURSOR FOR
    SELECT
        MaNV,
        HoTen,
        LuongCoBan,
        CASE
            WHEN LuongCoBan < 10000000 THEN N'Thấp'
            WHEN LuongCoBan < 15000000 THEN N'Trung bình'
            WHEN LuongCoBan < 20000000 THEN N'Khá'
            ELSE N'Cao'
        END AS XepLoaiLuong
    FROM NHANVIEN
    WHERE MaPB = @MaPB
    ORDER BY MaNV;

    OPEN CursorNhanVien;

    FETCH NEXT FROM CursorNhanVien
    INTO @MaNV, @HoTen, @LuongCoBan, @XepLoaiLuong;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        PRINT @MaNV + N' - ' + @HoTen + N' - ' + @XepLoaiLuong;

        FETCH NEXT FROM CursorNhanVien
        INTO @MaNV, @HoTen, @LuongCoBan, @XepLoaiLuong;
    END

    CLOSE CursorNhanVien;
    DEALLOCATE CursorNhanVien;

    PRINT N'=====================================';

    FETCH NEXT FROM CursorPhongBan INTO @MaPB, @TenPB;
END

CLOSE CursorPhongBan;
DEALLOCATE CursorPhongBan;
GO

