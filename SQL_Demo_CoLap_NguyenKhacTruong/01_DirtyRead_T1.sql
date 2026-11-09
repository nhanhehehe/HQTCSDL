USE [IsolationLevelDemo_NguyenKhacTruong];
GO

CREATE OR ALTER PROCEDURE dbo.nt_DirtyRead_T1
    @SoTienThem int = 50000,
    @MaTK varchar(10) = 'A001'
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    UPDATE dbo.TaiKhoan
    SET SoTien = SoTien + @SoTienThem
    WHERE MaTK = @MaTK;
    WAITFOR DELAY '00:00:10';
    ROLLBACK TRANSACTION;
END;
GO

EXEC dbo.nt_DirtyRead_T1;
