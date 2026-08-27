CREATE OR REPLACE FUNCTION demo_sales.list_subscribed_regions
RETURN CLOB
AUTHID CURRENT_USER
IS
    c_tenancy_ocid CONSTANT VARCHAR2(4000) :=
        'ocid1.tenancy.oc1..aaaaaaaask2zr4ol2uunuhtdhpugwisxqmbjfdzbjjbhqymhnhbekxowolga';

    -- Use your tenancy home region, for example us-ashburn-1.
    c_identity_region CONSTANT VARCHAR2(100) :=
        'us-ashburn-1';

    l_response      DBMS_CLOUD_TYPES.resp;
    l_response_body CLOB;
    l_status        PLS_INTEGER;
BEGIN
    l_response := DBMS_CLOUD.SEND_REQUEST(
        credential_name => 'OCI$RESOURCE_PRINCIPAL',
        uri             => 'https://identity.' || c_identity_region ||
                           '.oraclecloud.com/20160918/tenancies/' ||
                           c_tenancy_ocid || '/regionSubscriptions',
        method          => DBMS_CLOUD.METHOD_GET
    );

    l_status := DBMS_CLOUD.GET_RESPONSE_STATUS_CODE(l_response);
    l_response_body := DBMS_CLOUD.GET_RESPONSE_TEXT(l_response);

    IF l_status <> 200 THEN
        RAISE_APPLICATION_ERROR(
            -20006,
            'OCI ListRegionSubscriptions failed (HTTP ' || l_status || '): ' ||
            DBMS_LOB.SUBSTR(l_response_body, 1800, 1)
        );
    END IF;

    RETURN l_response_body;
END;
/