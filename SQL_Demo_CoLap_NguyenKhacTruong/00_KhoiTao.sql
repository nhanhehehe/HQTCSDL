-- Khởi tạo dữ liệu
IF DB_ID(N'IsolationLevelDemo_NguyenKhacTruong') IS NULL
    EXEC(N'CREATE DATABASE [IsolationLevelDemo_NguyenKhacTruong]');
GO

USE [IsolationLevelDemo_NguyenKhacTruong];
GO

IF OBJECT_ID(N'dbo.TaiKhoan', N'U') IS NOT NULL
    DROP TABLE dbo.TaiKhoan;
GO

CREATE TABLE dbo.TaiKhoan
(
    MaTK  varchar(10) NOT NULL CONSTRAINT PK_TaiKhoan PRIMARY KEY,
    TenTK nvarchar(100) NOT NULL,
    SoTien int NOT NULL
);
GO

INSERT INTO dbo.TaiKhoan (MaTK, TenTK, SoTien)
VALUES
    ('A001', N'NguyenKhacTruong', 100000),
    ('A002', N'NguyenKhacTruong', 200000),
    ('A003', N'NguyenKhacTruong', 50000);
GO

SELECT * FROM dbo.TaiKhoan ORDER BY MaTK;
GO
