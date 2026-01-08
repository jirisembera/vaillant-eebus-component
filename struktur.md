# Struktur
```mermaid
graph TD
    %% Tier 1: Device
    T1[<b>Tier 1: Device</b><br/>Vaillant VR921 Gateway] 

    %% Tier 2: Entities
    subgraph T2 [Tier 2: Entities]
        E0[entity=0: NodeMgmt]
        E31[entity=3,1: Compressor]
        E4[entity=4: DHW Circuit]
        E511[entity=5,1,1: HVAC Room]
        E6[entity=6: Temp Sensor]
    end

    %% Tier 3: Features & Operations
    subgraph T3 [Tier 3: Features & Operations]
        %% Entity 0
        F0[f=0: NodeManagement]
        F1[f=1: DeviceClass]
        
        %% Compressor
        F11_C[f=11: Measurement]
        F19[f=19: SmartEnergy]
        
        %% Warmwasser
        F11_W[f=11: Measurement]
        F18_W[f=18: Setpoint]
        
        %% Heizkreis
        F11_R[f=11: Measurement]
        F18_R[f=18: Setpoint]
        
        %% Außen
        F11_A[f=11: Measurement]
    end

    %% Actions / Commands (Aus deinem Log)
    subgraph Actions [<b>SPINE Operations</b>]
        Read_Class[READ: Manufacturer/UserData]
        Read_Meas[READ: Description/ListData]
        Read_Set[READ: SetpointDesc/List/Constraints]
        Sub_Call[CALL: SubscriptionRequest]
        Notify_Only[SKIP READ: Notify-only Mode]
    end

    %% Mapping Tier 1 to 2
    T1 --> E0 & E31 & E4 & E511 & E6

    %% Mapping Tier 2 to 3
    E0 --> F0 & F1
    E31 --> F11_C & F19
    E4 --> F11_W & F18_W
    E511 --> F11_R & F18_R
    E6 --> F11_A

    %% Mapping Actions to Features (Basierend auf Log)
    Read_Class -.-> F1
    Read_Meas -.-> F11_C & F11_W & F11_R
    Read_Set  -.-> F18_W & F18_R
    Sub_Call  -- "Abo aktiv" --> F18_W & F18_R & F11_C
    Notify_Only -.-> F11_A

    %% Styling
    style T1 fill:#f9f,stroke:#333,stroke-width:2px
    style Actions fill:#fff4dd,stroke:#d4a017,stroke-dasharray: 5 5
    style Notify_Only fill:#ffcccc,stroke:#cc0000
    style Sub_Call fill:#d4edda,stroke:#28a745
