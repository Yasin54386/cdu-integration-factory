-- Export query: defines the exact columns and order of the output file.

SELECT STUDENT_ID,
       FAMILY_NAME,
       GIVEN_NAME,
       COURSE_CODE,
       TO_CHAR(ENROLLED_DATE, 'YYYY-MM-DD') AS ENROLLED_DATE
FROM   STG_STUDENT_DOWNLOAD_V1
ORDER  BY STUDENT_ID;
