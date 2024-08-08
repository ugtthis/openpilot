#pragma once

#include <QLabel>
#include <QWidget>
#include <QHBoxLayout>

class InfoDisplayBar : public QWidget {
  Q_OBJECT

public:
  explicit InfoDisplayBar(QWidget* parent = nullptr);
  void setMessage(const QString &message, const QString &iconPath = QString());

private:
  QLabel *iconLabel;
  QLabel *messageLabel;
  QHBoxLayout *layout;
};