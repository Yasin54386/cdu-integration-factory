-- Loads active students into the job's namespaced staging table.
-- The factory namespaces the table as STG_STUDENT_DOWNLOAD_V1 (spec D11).

BEGIN
  EXECUTE IMMEDIATE 'TRUNCATE TABLE STG_STUDENT_DOWNLOAD_V1';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -942 THEN RAISE; END IF;  -- table may not exist yet
END;
/

INSERT INTO STG_STUDENT_DOWNLOAD_V1 (STUDENT_ID, FAMILY_NAME, GIVEN_NAME, COURSE_CODE, ENROLLED_DATE)
SELECT s.student_id,
       s.family_name,
       s.given_name,
       e.course_code,
       e.enrolled_date
FROM   students s
JOIN   enrolments e ON e.student_id = s.student_id
WHERE  e.status = 'ACTIVE';
