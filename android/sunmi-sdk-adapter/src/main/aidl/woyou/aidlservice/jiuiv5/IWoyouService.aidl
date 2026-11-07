package woyou.aidlservice.jiuiv5;

import woyou.aidlservice.jiuiv5.ICallback;

/**
 * SUNMI Woyou AIDL Service Interface (woyou.aidlservice.jiuiv5.IWoyouService).
 * Provides IPC bindings to the built-in SUNMI thermal printer and RJ11/RJ12 cash drawer controller.
 */
interface IWoyouService {
    /**
     * Initializes/resets the printer logic state.
     */
    void printerInit(in ICallback callback);

    /**
     * Polls the current operational state of the thermal printer:
     * 1 = Normal
     * 2 = Preparing / Firmware update
     * 3 = Abnormal communication
     * 4 = Out of paper
     * 5 = Overheated
     * 6 = Cover open
     * 7 = Cutter error
     * 8 = Cutter recovered
     * 9 = Black mark not found
     * 505 = No printer detected
     * 507 = Firmware update failed
     */
    int updatePrinterState();

    /**
     * Sends raw ESC/POS bytes directly to the printer hardware controller.
     */
    void sendRAWData(in byte[] data, in ICallback callback);

    /**
     * Prints UTF-8 formatted text with current alignment and font style.
     */
    void printText(String text, in ICallback callback);

    /**
     * Sets text alignment: 0 = Left, 1 = Center, 2 = Right.
     */
    void setAlignment(int alignment, in ICallback callback);

    /**
     * Sets font size in points.
     */
    void setFontSize(float fontsize, in ICallback callback);

    /**
     * Advances paper by n lines.
     */
    void lineWrap(int n, in ICallback callback);

    /**
     * Prints a 1D barcode.
     */
    void printBarCode(String data, int symbology, int height, int width, int textposition, in ICallback callback);

    /**
     * Prints a 2D QR code.
     */
    void printQRCode(String data, int modulesize, int errorlevel, in ICallback callback);

    /**
     * Cuts the paper (supported on desktop terminals with auto-cutter such as T2/T2s/T3).
     */
    void cutPaper(in ICallback callback);

    /**
     * Sends an electrical solenoid pulse to the RJ11/RJ12 cash drawer port.
     */
    void openDrawer(in ICallback callback);

    /**
     * Returns the cumulative count of drawer open pulses since device boot/reset.
     * Note: Does NOT indicate whether the drawer is currently open or closed.
     */
    int getOpenDrawerTimes();

    /**
     * Reads the real-time electrical microswitch status of the cash drawer port.
     * True/False polarity depends on terminal model and drawer wiring (calibrated per model).
     */
    boolean getDrawerStatus();

    /**
     * Returns hardware serial number of the SUNMI terminal.
     */
    String getPrinterSerialNo();

    /**
     * Returns hardware model identifier (e.g., "SUNMI T2", "SUNMI D2s", "V2_PRO").
     */
    String getPrinterModal();

    /**
     * Returns printer firmware version string.
     */
    String getPrinterVersion();

    /**
     * Returns Woyou AIDL service package version string.
     */
    String getServiceVersion();
}
