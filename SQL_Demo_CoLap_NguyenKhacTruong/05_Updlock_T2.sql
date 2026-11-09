USE [IsolationLevelDemo_NguyenKhacTruong];
GO

CREATE OR ALTER PROCEDURE dbo.nt_Updlock_T2
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    DECLARE @SoDu int;
    SELECT @SoDu = SoTien
    FROM dbo.TaiKhoan WITH (UPDLOCK)
    WHERE MaTK = 'A001';
    SET @SoDu = @SoDu + 300000;
    UPDATE dbo.TaiKhoan SET SoTien = @SoDu WHERE MaTK = 'A001';
    COMMIT TRANSACTION;

    SELECT * FROM dbo.TaiKhoan WHERE MaTK = 'A001';
END;
GO

EXEC dbo.nt_Updlock_T2;
