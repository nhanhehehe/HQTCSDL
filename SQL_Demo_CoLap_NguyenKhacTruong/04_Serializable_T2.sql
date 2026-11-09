USE [IsolationLevelDemo_NguyenKhacTruong];
GO

CREATE OR ALTER PROCEDURE dbo.nt_Serializable_T2
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    INSERT INTO dbo.TaiKhoan (MaTK, TenTK, SoTien)
    VALUES ('B002', N'NguyenKhacTruong', 500000);
    COMMIT TRANSACTION;
END;
GO

EXEC dbo.nt_Serializable_T2;
