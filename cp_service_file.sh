cp P_WEB_YSAN.WIN.service /etc/systemd/system/P_WEB_YSAN.WIN.service
systemctl daemon-reload
systemctl enable P_WEB_YSAN.WIN.service
systemctl start P_WEB_YSAN.WIN.service