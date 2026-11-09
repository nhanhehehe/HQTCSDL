USE [IsolationLevelDemo_NguyenKhacTruong];
GO

CREATE OR ALTER PROCEDURE dbo.nt_UnrepeatableRead_T2
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    UPDATE dbo.TaiKhoan SET SoTien = 900000 WHERE MaTK = 'A001';
    COMMIT TRANSACTION;
END;
GO

EXEC dbo.nt_UnrepeatableRead_T2;
